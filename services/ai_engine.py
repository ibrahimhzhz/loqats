from datetime import datetime
from google import genai
from google.genai import types as genai_types
import json
import os
import asyncio
import concurrent.futures
import time
import re
import logging
from dotenv import dotenv_values
from pathlib import Path
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

# --- 1. SETUP & AUTHENTICATION ---

# Try to find the .env file
base_dir = Path(__file__).resolve().parent.parent
env_path = base_dir / ".env"

# Attempt to load from .env
config = dotenv_values(env_path)
project_id = os.getenv("GCP_PROJECT_ID") or config.get("GCP_PROJECT_ID")
location = os.getenv("GCP_LOCATION") or config.get("GCP_LOCATION", "us-central1")  # Default to us-central1

# --- Gemini Developer API (AI Studio) key + model selection ---
# If a GEMINI_API_KEY / GOOGLE_API_KEY is present we use the free-tier Gemini
# Developer API. Otherwise we fall back to Vertex AI using the GCP project.
gemini_api_key = (
    os.getenv("GEMINI_API_KEY")
    or os.getenv("GOOGLE_API_KEY")
    or config.get("GEMINI_API_KEY")
    or config.get("GOOGLE_API_KEY")
)
GENERATION_MODEL = os.getenv("GEMINI_MODEL") or config.get("GEMINI_MODEL", "gemini-2.5-flash")
EMBEDDING_MODEL = os.getenv("GEMINI_EMBEDDING_MODEL") or config.get("GEMINI_EMBEDDING_MODEL", "gemini-embedding-001")
try:
    EMBEDDING_DIM = int(os.getenv("GEMINI_EMBEDDING_DIM") or config.get("GEMINI_EMBEDDING_DIM", "768"))
except (TypeError, ValueError):
    EMBEDDING_DIM = 768

# Support injecting credentials directly as JSON (or base64 JSON) via environment.
# Accept multiple env var names for deployment platform compatibility.
credentials_json = (
    os.getenv("GOOGLE_APPLICATION_CREDENTIALS_JSON")
    or os.getenv("GOOGLE_CREDENTIALS_BASE64")
    or os.getenv("GOOGLE_CREDENTIALS_JSON")
)
if credentials_json:
    try:
        import base64
        decoded = base64.b64decode(credentials_json).decode("utf-8")
        parsed = json.loads(decoded)
        normalized_json = json.dumps(parsed)
    except Exception:
        parsed = json.loads(credentials_json)
        normalized_json = json.dumps(parsed)

    runtime_creds_path = "/tmp/gcp-service-account.json"
    with open(runtime_creds_path, "w", encoding="utf-8") as credentials_file:
        credentials_file.write(normalized_json)
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = runtime_creds_path
    print(f"✅ Using credentials from GOOGLE_APPLICATION_CREDENTIALS_JSON at: {runtime_creds_path}")

# Set Google Cloud credentials path if specified
credentials_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS") or config.get("GOOGLE_APPLICATION_CREDENTIALS")
if credentials_path:
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = credentials_path
    print(f"✅ Using credentials from: {credentials_path}")

_client = None
_model = None
_embedding_model = None
_init_error = None


class AIServiceUnavailableError(RuntimeError):
    """Raised when AI extraction is requested but the Gemini API is unavailable."""


class GenerationConfig:
    """
    Backward-compatible shim for the old Vertex `GenerationConfig`. Call sites
    still write `GenerationConfig(temperature=0.0, response_mime_type=...)`;
    the adapter below translates it to the google-genai config at call time.
    """
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class _GenModelAdapter:
    """
    Presents the old `model.generate_content(prompt, generation_config=...)`
    interface on top of the unified google-genai client, so none of the
    existing generation call sites need to change.
    """
    def __init__(self, client, model_name):
        self._client = client
        self._model = model_name

    def generate_content(self, prompt, generation_config=None):
        cfg_kwargs = {}
        if generation_config is not None:
            cfg_kwargs = getattr(generation_config, "kwargs", None)
            if cfg_kwargs is None:
                cfg_kwargs = generation_config if isinstance(generation_config, dict) else {}
        config = genai_types.GenerateContentConfig(**cfg_kwargs) if cfg_kwargs else None
        return self._client.models.generate_content(
            model=self._model, contents=prompt, config=config
        )


class _EmbedModelAdapter:
    """
    Presents the old `embedding_model.get_embeddings(list_of_texts)` interface,
    returning objects with a `.values` attribute, on top of the unified client.
    """
    def __init__(self, client, model_name, dim):
        self._client = client
        self._model = model_name
        self._dim = dim

    def get_embeddings(self, texts):
        config = (
            genai_types.EmbedContentConfig(output_dimensionality=self._dim)
            if self._dim else None
        )
        resp = self._client.models.embed_content(
            model=self._model, contents=list(texts), config=config
        )
        # resp.embeddings is a list of ContentEmbedding, each with `.values`.
        return list(resp.embeddings)


def _get_client():
    """Lazy-init the unified google-genai client (API key first, Vertex fallback)."""
    global _client, _init_error

    if _client is not None:
        return _client
    if _init_error is not None:
        return None

    try:
        if gemini_api_key:
            _client = genai.Client(api_key=gemini_api_key)
            print("✅ Gemini API client initialized (AI Studio / Developer API)")
        elif project_id:
            _client = genai.Client(vertexai=True, project=project_id, location=location)
            print(f"✅ Gemini client initialized via Vertex AI: {project_id}/{location}")
        else:
            _init_error = (
                "No GEMINI_API_KEY (or GOOGLE_API_KEY) and no GCP_PROJECT_ID set. "
                "AI extraction is unavailable. Get a free key at https://aistudio.google.com/apikey"
            )
            print(f"⚠️ {_init_error}")
            return None
        return _client
    except Exception as exc:
        _init_error = f"Gemini client initialization failed: {exc}"
        print(f"⚠️ {_init_error}")
        return None


def _get_model():
    """Lazy-init the generation model adapter."""
    global _model
    if _model is not None:
        return _model
    client = _get_client()
    if client is None:
        return None
    _model = _GenModelAdapter(client, GENERATION_MODEL)
    return _model


def _get_embedding_model():
    """Lazy-init the embedding model adapter for semantic skill matching."""
    global _embedding_model
    if _embedding_model is not None:
        return _embedding_model
    client = _get_client()
    if client is None:
        return None
    _embedding_model = _EmbedModelAdapter(client, EMBEDDING_MODEL, EMBEDDING_DIM)
    return _embedding_model


def is_ai_available() -> bool:
    """Return True when the Gemini client can be used for extraction."""
    return _get_client() is not None


def get_ai_unavailable_reason() -> Optional[str]:
    """Return initialization error reason when AI is unavailable."""
    _get_client()
    return _init_error


# --- RATE LIMITING ---
class RateLimiter:
    """Simple rate limiter for API calls."""
    
    def __init__(self, calls_per_minute: int = 15):
        self.calls_per_minute = calls_per_minute
        self.min_interval = 60.0 / calls_per_minute
        self.last_call_time = 0
    
    async def wait_if_needed(self):
        """Wait if necessary to respect rate limits."""
        current_time = time.time()
        time_since_last_call = current_time - self.last_call_time
        
        if time_since_last_call < self.min_interval:
            wait_time = self.min_interval - time_since_last_call
            print(f"⏳ Rate limiting: waiting {wait_time:.2f}s...")
            await asyncio.sleep(wait_time)
        
        self.last_call_time = time.time()


# Global rate limiter. The Gemini API free tier (AI Studio) is much stricter
# than Vertex — often ~10-15 requests/minute per model. Default to 10 to stay
# under the free-tier ceiling; raise GEMINI_RPM on a paid tier or on Vertex.
try:
    _rpm = int(os.getenv("GEMINI_RPM") or config.get("GEMINI_RPM", "10"))
except (TypeError, ValueError):
    _rpm = 10
rate_limiter = RateLimiter(calls_per_minute=_rpm)
EMBEDDING_BATCH_SIZE = 250
SKILL_EMBEDDING_PREFIX = "Resume skill: "


# --- 2. HELPER FUNCTIONS ---
def clean_json_string(text):
    """
    Cleans API response if it includes markdown formatting like ```json ... ```
    """
    text = text.strip()
    # Remove markdown code blocks if present
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove first line if it's ```json and last line if it's ```
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines)
    return text


EMAIL_PATTERN = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
PHONE_PATTERN = re.compile(r"(?:\+?\d[\d\s().-]{7,}\d)")


DEFAULT_JD_REQUIREMENTS: Dict[str, Any] = {
    "must_have_skills": [],
    "minimum_years_experience": 0.0,
    "education_requirement": "Not specified",
    "offers_visa_sponsorship": None,
}


CANDIDATE_FACT_SCHEMA: Dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "name": {
            "type": "STRING",
            "nullable": True,
            "description": "Candidate full name exactly as written on the resume header. null if not present.",
        },
        "email": {
            "type": "STRING",
            "nullable": True,
            "description": "Candidate email address as written in the resume. null if not present.",
        },
        "phone": {
            "type": "STRING",
            "nullable": True,
            "description": "Candidate phone number as written in the resume. null if not present.",
        },
        "extractable_text": {
            "type": "BOOLEAN",
            "description": "true if the resume contained readable text. false if the document appeared blank, corrupted, or image-only with no parseable content.",
        },
        "requires_visa_sponsorship": {
            "type": "BOOLEAN",
            "description": "true if the candidate explicitly mentions needing visa sponsorship, work authorization, or OPT/CPT/H1B. false if not mentioned or if they state they are authorized.",
        },
        "has_measurable_impact": {
            "type": "BOOLEAN",
            "description": "true if the resume contains at least one quantified achievement using numbers, percentages, or dollar amounts (e.g. 'increased revenue by 30%', 'reduced latency by 200ms', 'managed $2M budget'). false otherwise.",
        },
        "has_contact_info": {
            "type": "BOOLEAN",
            "description": "true if the resume contains at least an email address or phone number.",
        },
        "has_clear_job_titles": {
            "type": "BOOLEAN",
            "description": "true if each role in the work history has a clearly stated job title.",
        },
        "skills": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "name": {
                        "type": "STRING",
                        "description": "The skill name exactly as it appears or can be clearly inferred from context. Lowercase.",
                    },
                    "last_used_year": {
                        "type": "INTEGER",
                        "nullable": True,
                        "description": "The year this skill was most recently used based on the job it appears in. null if cannot be determined.",
                    },
                    "job_index": {
                        "type": "INTEGER",
                        "nullable": True,
                        "description": "Zero-based index into the jobs array indicating which job this skill was most recently associated with. null if cannot be determined.",
                    },
                },
                "required": ["name"],
            },
            "description": "Every technical skill, tool, framework, language, and platform mentioned anywhere in the resume.",
        },
        "education": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "degree": {
                        "type": "STRING",
                        "description": "One of: none, high school, associate, bachelor, master, phd. Lowercase. Map all variations (e.g. 'BS', 'B.Sc', 'Bachelor of Science' all map to 'bachelor').",
                    },
                    "field_of_study": {
                        "type": "STRING",
                        "nullable": True,
                        "description": "The major or field of study, lowercase. e.g. 'computer science', 'mathematics', 'business administration'. null if not specified.",
                    },
                    "institution": {
                        "type": "STRING",
                        "nullable": True,
                        "description": "Name of the university or school. null if not specified.",
                    },
                    "year": {
                        "type": "INTEGER",
                        "nullable": True,
                        "description": "Graduation year. null if not specified.",
                    },
                },
                "required": ["degree"],
            },
            "description": "All education entries from the resume.",
        },
        "jobs": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "title": {
                        "type": "STRING",
                        "description": "The exact job title as stated on the resume.",
                    },
                    "company": {
                        "type": "STRING",
                        "description": "The company name.",
                    },
                    "start_year": {
                        "type": "INTEGER",
                        "description": "The year this role started.",
                    },
                    "end_year": {
                        "type": "INTEGER",
                        "nullable": True,
                        "description": "The year this role ended. null if this is the current role.",
                    },
                    "is_current": {
                        "type": "BOOLEAN",
                        "description": "true if this is the candidate's current or most recent active role.",
                    },
                    "domain": {
                        "type": "STRING",
                        "description": "The industry domain of this company. One of: fintech, healthcare, saas, ecommerce, enterprise, agency, startup, government, education, media, logistics, other.",
                    },
                    "work_type": {
                        "type": "STRING",
                        "description": "One of: remote, hybrid, onsite, unknown. Infer from the resume if explicitly stated. unknown if not stated.",
                    },
                },
                "required": ["title", "company", "start_year", "is_current", "domain", "work_type"],
            },
            "description": "Work history in reverse chronological order. Index 0 is always the most recent role.",
        },
        "cover_letter_analysis": {
            "type": "OBJECT",
            "properties": {
                "word_count": {
                    "type": "INTEGER",
                    "description": "Total word count of the cover letter. 0 if no cover letter was provided.",
                },
                "mentions_role_title": {
                    "type": "BOOLEAN",
                    "description": "true if the cover letter explicitly mentions the job title or a close variation of it. false if no cover letter or title not mentioned.",
                },
                "skills_mentioned": {
                    "type": "ARRAY",
                    "items": {"type": "STRING"},
                    "description": "List of technical skills mentioned in the cover letter, lowercase. Empty array if none or no cover letter.",
                },
                "has_specific_example": {
                    "type": "BOOLEAN",
                    "description": "true if the cover letter contains at least one specific example of past work, achievement, or project with concrete details. false otherwise.",
                },
                "is_generic": {
                    "type": "BOOLEAN",
                    "description": "true if the cover letter appears to be a generic template with no role-specific content. false if it contains specific relevant content.",
                },
            },
            "required": ["word_count", "mentions_role_title", "skills_mentioned", "has_specific_example", "is_generic"],
        },
        "custom_answer_analysis": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "question_index": {
                        "type": "INTEGER",
                        "description": "Zero-based index of the question this answer corresponds to.",
                    },
                    "word_count": {
                        "type": "INTEGER",
                        "description": "Word count of the answer.",
                    },
                    "is_relevant": {
                        "type": "BOOLEAN",
                        "description": "true if the answer is topically relevant to the question asked. false if off-topic, nonsensical, or placeholder text.",
                    },
                    "has_specific_example": {
                        "type": "BOOLEAN",
                        "description": "true if the answer contains a specific real example, project, or measurable outcome. false if entirely vague or generic.",
                    },
                },
                "required": ["question_index", "word_count", "is_relevant", "has_specific_example"],
            },
            "description": "Analysis of custom question answers. Empty array if no custom answers provided.",
        },
    },
    "required": [
        "name",
        "extractable_text",
        "requires_visa_sponsorship",
        "has_measurable_impact",
        "has_contact_info",
        "has_clear_job_titles",
        "skills",
        "education",
        "jobs",
        "cover_letter_analysis",
        "custom_answer_analysis",
    ],
}


EXTRACTION_SYSTEM_PROMPT = """You are a resume data extraction API. Your only function is to extract factual information from the provided resume text and return it as a strict JSON object.

Rules you must follow without exception:
1. Extract only facts that are explicitly stated or can be directly calculated from stated dates. Never infer, guess, or assume anything that is not written.
2. Never evaluate, score, rank, or make any judgment about the candidate's quality, suitability, or skills. You are forbidden from producing any subjective assessment.
3. If a piece of information is not present in the resume, return null for that field. Never fabricate data to fill a field.
4. Extract candidate name from document structure (header/contact block) instead of generic section titles like 'Curriculum Vitae' or 'Professional Summary'.
5. For the jobs array, list roles in reverse chronological order — most recent first. Index 0 is always the most recent role.
6. For skills, extract every technical skill, tool, framework, language, and platform mentioned anywhere in the resume including job descriptions, skills sections, and project descriptions.
7. For degree, map all variations to the canonical values: none, high school, associate, bachelor, master, phd.
8. For domain, use your knowledge of the company to determine the industry domain. If the company is unknown or ambiguous, use "other".
9. For work_type, only mark remote or hybrid if the resume explicitly states it. Otherwise use unknown.
10. Return only the JSON object. No explanation, no preamble, no markdown formatting, no code fences."""


def validate_extraction_result(raw: Dict[str, Any]) -> Dict[str, Any]:
    """
    Standalone pure validation function for Gemini extraction results.

    Takes the raw JSON response from Gemini and returns a clean, guaranteed-shape
    object. Logs warnings for any field that required correction.

    This function must never be inlined into the Celery task.
    """
    result = dict(raw)  # shallow copy

    # 1. extractable_text — must be boolean. If false, return null shell immediately.
    if not isinstance(result.get("extractable_text"), bool):
        logger.warning("Extraction validation: extractable_text missing or not boolean, defaulting to True")
        result["extractable_text"] = True

    if result["extractable_text"] is False:
        logger.warning("Extraction validation: extractable_text is False — resume not readable")
        return {
            "name": "Unknown Candidate",
            "email": None,
            "phone": None,
            "extractable_text": False,
            "total_years_experience": 0,
            "requires_visa_sponsorship": False,
            "has_measurable_impact": False,
            "has_contact_info": False,
            "has_clear_job_titles": False,
            "employment_gaps": False,
            "average_tenure_years": 0.0,
            "skills": [],
            "education": [],
            "jobs": [],
            "cover_letter_analysis": {
                "word_count": 0,
                "mentions_role_title": False,
                "skills_mentioned": [],
                "has_specific_example": False,
                "is_generic": True,
            },
            "custom_answer_analysis": [],
        }

    # 2. name — required string, but fallback to Unknown Candidate
    if not isinstance(result.get("name"), str) or not result["name"].strip():
        logger.warning("Extraction validation: name missing or invalid, defaulting to Unknown Candidate")
        result["name"] = "Unknown Candidate"
    else:
        result["name"] = result["name"].strip()

    # 3. email / phone — optional strings; keep None when invalid
    email = result.get("email")
    if isinstance(email, str):
        email = email.strip().lower()
        result["email"] = email if email else None
    elif email is None:
        result["email"] = None
    else:
        logger.warning("Extraction validation: email was not a string, defaulting to None")
        result["email"] = None

    phone = result.get("phone")
    if isinstance(phone, str):
        phone = phone.strip()
        result["phone"] = phone if phone else None
    elif phone is None:
        result["phone"] = None
    else:
        logger.warning("Extraction validation: phone was not a string, defaulting to None")
        result["phone"] = None

    # 4. skills — array, every item must have at least a 'name' field
    skills = result.get("skills")
    if not isinstance(skills, list):
        logger.warning("Extraction validation: skills was not an array, setting to []")
        skills = []
    valid_skills = []
    for s in skills:
        if isinstance(s, dict) and s.get("name") and isinstance(s["name"], str) and s["name"].strip():
            valid_skills.append(s)
        else:
            logger.warning(f"Extraction validation: dropping skill entry missing name: {s}")
    result["skills"] = valid_skills

    # 5. jobs — array, every item must have title, company, start_year
    jobs = result.get("jobs")
    if not isinstance(jobs, list):
        logger.warning("Extraction validation: jobs was not an array, setting to []")
        jobs = []
    valid_jobs = []
    for j in jobs:
        if (isinstance(j, dict)
                and j.get("title") and isinstance(j["title"], str) and j["title"].strip()
                and j.get("company") and isinstance(j["company"], str) and j["company"].strip()
                and j.get("start_year") is not None):
            valid_jobs.append(j)
        else:
            logger.warning(f"Extraction validation: dropping job entry missing required fields: {j}")
    result["jobs"] = valid_jobs

    # 6. education — must be an array, never null
    education = result.get("education")
    if not isinstance(education, list):
        logger.warning("Extraction validation: education was not an array, setting to []")
        result["education"] = []

    # 7. Boolean fields — must be boolean, default False
    for field in [
        "requires_visa_sponsorship",
        "has_measurable_impact",
        "has_contact_info",
        "has_clear_job_titles",
        "employment_gaps",
    ]:
        if not isinstance(result.get(field), bool):
            logger.warning(f"Extraction validation: {field} not boolean, defaulting to False")
            result[field] = False

    # 8. average_tenure_years — must be float
    try:
        result["average_tenure_years"] = round(float(result.get("average_tenure_years") or 0.0), 1)
    except (TypeError, ValueError):
        logger.warning("Extraction validation: average_tenure_years not parseable, setting to 0.0")
        result["average_tenure_years"] = 0.0

    # 9. cover_letter_analysis — must be a dict with required keys
    cla = result.get("cover_letter_analysis")
    if not isinstance(cla, dict):
        logger.warning("Extraction validation: cover_letter_analysis missing, setting default")
        result["cover_letter_analysis"] = {
            "word_count": 0,
            "mentions_role_title": False,
            "skills_mentioned": [],
            "has_specific_example": False,
            "is_generic": True,
        }

    # 10. custom_answer_analysis — must be an array
    caa = result.get("custom_answer_analysis")
    if not isinstance(caa, list):
        logger.warning("Extraction validation: custom_answer_analysis not array, setting to []")
        result["custom_answer_analysis"] = []

    return result


def merge_required_skills(manual_skills, extracted_skills) -> list:
    """
    Union recruiter-entered required skills with any additional skills the AI
    extracted from the free-text job description, deduplicated case- and
    alias-insensitively (via scoring.normalize_skill).

    This exists because every screening entry point previously did
    `extracted_skills or manual_skills` — a boolean OR that let a partial
    AI extraction silently *replace* the recruiter's deliberately-typed list
    whenever the extraction returned anything at all. A recruiter who typed
    "Python, React, PostgreSQL, AWS" could see PostgreSQL and AWS silently
    dropped just because the JD text didn't happen to name them, even though
    the recruiter explicitly required them. The recruiter's input is always
    authoritative here; extraction only adds to it, never subtracts.
    """
    from scoring import normalize_skill  # local import: keep ai_engine standalone-importable

    merged: list = []
    seen: set = set()
    for skill in list(manual_skills or []) + list(extracted_skills or []):
        text = str(skill).strip()
        if not text:
            continue
        key = normalize_skill(text)
        if key in seen:
            continue
        seen.add(key)
        merged.append(text)
    return merged


def normalize_job_requirements(job_requirements: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Normalize extracted job requirements to a strict, typed schema."""
    if not isinstance(job_requirements, dict):
        return dict(DEFAULT_JD_REQUIREMENTS)

    skills = job_requirements.get("must_have_skills") or []
    if not isinstance(skills, list):
        skills = []
    normalized_skills = []
    for skill in skills:
        val = str(skill).strip()
        if val:
            normalized_skills.append(val)

    try:
        min_years = float(job_requirements.get("minimum_years_experience") or 0.0)
    except (TypeError, ValueError):
        min_years = 0.0
    min_years = max(0.0, min_years)

    education_requirement = str(
        job_requirements.get("education_requirement") or "Not specified"
    ).strip() or "Not specified"

    visa_value = job_requirements.get("offers_visa_sponsorship")
    if isinstance(visa_value, bool):
        offers_visa_sponsorship = visa_value
    elif visa_value is None:
        offers_visa_sponsorship = None
    else:
        text = str(visa_value).strip().lower()
        if text in {"true", "yes", "y", "1"}:
            offers_visa_sponsorship = True
        elif text in {"false", "no", "n", "0"}:
            offers_visa_sponsorship = False
        else:
            offers_visa_sponsorship = None

    return {
        "must_have_skills": normalized_skills,
        "minimum_years_experience": min_years,
        "education_requirement": education_requirement,
        "offers_visa_sponsorship": offers_visa_sponsorship,
    }

# --- 3. LEVEL 1: JD REQUIREMENT EXTRACTION (Grounded) ---

async def extract_jd_requirements(jd_text: str) -> Dict[str, Any]:
    """
    LEVEL 1: Strict JD normalization.

    Converts a freeform job description into a deterministic JSON contract used
    by downstream screening and scoring logic.
    """
    print("📋 Extracting structured JD requirements (Level 1 baseline)...")
    await rate_limiter.wait_if_needed()
    model = _get_model()
    if model is None:
        return dict(DEFAULT_JD_REQUIREMENTS)

    prompt = f"""
        You are a strict information extractor.
        Convert the job description into a strict JSON object with EXACTLY these keys:
        - must_have_skills: array of canonical skill names (strings only)
        - minimum_years_experience: number (float or int)
        - education_requirement: string (single concise requirement)
        - offers_visa_sponsorship: true, false, or null if not specified

    JOB DESCRIPTION:
    {jd_text[:8000]}

        RULES:
        1) Include only hard requirements for must_have_skills.
        2) If years are unspecified, set minimum_years_experience to 0.
        3) Keep education_requirement as "Not specified" when absent.
        4) Do NOT add extra keys.
        5) Return ONLY JSON.
    
        Example:
        {{
            "must_have_skills": ["Python", "React", "PostgreSQL"],
            "minimum_years_experience": 3,
            "education_requirement": "Bachelor's in Computer Science or equivalent",
            "offers_visa_sponsorship": false
        }}
    """

    try:
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,
            lambda: model.generate_content(
                prompt,
                generation_config=GenerationConfig(
                    temperature=0.0,  # Maximum determinism
                    response_mime_type="application/json"
                )
            )
        )
        json_text = clean_json_string(response.text)
        requirements = normalize_job_requirements(json.loads(json_text))
        print(
            "✅ Structured JD extracted "
            f"({len(requirements['must_have_skills'])} skills, "
            f"{requirements['minimum_years_experience']} yrs min)"
        )
        return requirements

    except Exception as e:
        print(f"❌ JD REQUIREMENT EXTRACTION FAILED: {str(e)}")
        return dict(DEFAULT_JD_REQUIREMENTS)


# --- 4. LEVEL 2: GROUNDED RESUME VERIFICATION ---

# Degree ranking used for backward-compatible highest_education_level derivation
_DEGREE_RANK = {"none": 0, "high school": 1, "associate": 2, "bachelor": 3, "master": 4, "phd": 5}
_DEGREE_DISPLAY = {0: "None", 1: "High School", 2: "Associate", 3: "Bachelors", 4: "Masters", 5: "PhD"}


def _resolve_current_year(current_year: Optional[int] = None) -> int:
    """Return the caller-provided year or the runtime current UTC year."""
    if current_year is not None:
        return int(current_year)
    return datetime.utcnow().year


def _validated_contact_fields(
    extracted_email: Optional[str],
    extracted_phone: Optional[str],
    resume_text: str,
) -> tuple[str, str]:
    """Use LLM-extracted contact fields when valid; fallback to regex extraction."""
    email = (extracted_email or "").strip().lower() if isinstance(extracted_email, str) else ""
    if not email or not EMAIL_PATTERN.fullmatch(email):
        email_match = EMAIL_PATTERN.search(resume_text or "")
        email = email_match.group(0).lower() if email_match else "unknown@error.com"

    phone = (extracted_phone or "").strip() if isinstance(extracted_phone, str) else ""
    if not phone or not PHONE_PATTERN.search(phone):
        phone_match = PHONE_PATTERN.search(resume_text or "")
        phone = phone_match.group(0).strip() if phone_match else ""

    return email, phone


def calculate_total_years_experience(jobs: list, current_year: Optional[int] = None) -> float:
    """
    Sum the duration of every job in the extracted jobs list.
    Duration = end_year - start_year (use current_year if end_year is null).
    Skip entries with no start_year. Minimum duration per role is 0.
    Returns the total rounded to one decimal place.
    """
    active_year = _resolve_current_year(current_year)
    total = 0.0
    for job in jobs:
        start = job.get("start_year")
        if start is None:
            continue
        end = job.get("end_year") or active_year
        duration = max(end - start, 0)
        total += duration
    return round(total, 1)


def calculate_years_used(skill: dict, jobs: list, current_year: Optional[int] = None) -> float | None:
    """
    Calculates years a skill was used based on the duration of the job
    it was associated with. Uses job_index from the skill to look up
    the corresponding job in the jobs list and computes end - start.
    Returns None if job_index is missing or out of bounds.
    """
    active_year = _resolve_current_year(current_year)
    job_index = skill.get("job_index")
    if job_index is None:
        return None
    if job_index >= len(jobs):
        return None

    job = jobs[job_index]
    start = job.get("start_year")
    end = job.get("end_year") or active_year

    if start is None:
        return None

    duration = max(end - start, 0)
    return round(max(duration, 0.5), 1)  # minimum 0.5 to avoid zero for short contracts


def calculate_employment_gaps(jobs: list, current_year: Optional[int] = None) -> bool:
    """
    Returns True if a gap of MORE THAN ONE FULL CALENDAR YEAR exists between
    consecutive roles, or between the most recent role's end and the current
    year.

    Note on granularity: extraction only produces years, not months, so a
    "6-month gap" is not detectable. next_start - current_end == 1 can mean
    a gap of anywhere from 2 days (Dec → Jan) to ~23 months, so we only flag
    when the difference is >= 2 years — a gap that is guaranteed to contain
    at least one full uncovered calendar year. This trades recall for
    precision: no candidate gets a gap badge from a two-week job switch over
    New Year.

    Returns False if no dated jobs exist.
    """
    active_year = _resolve_current_year(current_year)
    if not jobs:
        return False

    sorted_jobs = sorted(
        [j for j in jobs if j.get("start_year")],
        key=lambda j: j["start_year"]
    )
    if not sorted_jobs:
        return False

    # Gaps between consecutive roles
    for i in range(len(sorted_jobs) - 1):
        current_end = sorted_jobs[i].get("end_year") or active_year
        next_start = sorted_jobs[i + 1].get("start_year", active_year)
        if next_start - current_end >= 2:
            return True

    # Trailing gap: most recent role ended and nothing since
    last_job = sorted_jobs[-1]
    if not last_job.get("is_current"):
        last_end = last_job.get("end_year")
        if last_end is not None and active_year - last_end >= 2:
            return True

    return False


def calculate_average_tenure(jobs: list, current_year: Optional[int] = None) -> float:
    """
    Calculates average years spent per role across all jobs in the list.
    Uses end_year if present, otherwise uses current_year for active roles.
    Returns 0.0 if the jobs list is empty.
    """
    active_year = _resolve_current_year(current_year)
    if not jobs:
        return 0.0

    tenures = []
    for job in jobs:
        start = job.get("start_year")
        if start is None:
            continue
        end = job.get("end_year") or active_year
        tenure = max(end - start, 0)
        tenures.append(tenure)

    if not tenures:
        return 0.0

    return round(sum(tenures) / len(tenures), 1)


def _derive_highest_education(education: List[Dict[str, Any]]) -> str:
    """Derive the highest education level string from the new education array for backward compat."""
    highest_rank = 0
    for entry in education:
        degree = str(entry.get("degree") or "none").strip().lower()
        rank = _DEGREE_RANK.get(degree, 0)
        if rank > highest_rank:
            highest_rank = rank
    return _DEGREE_DISPLAY.get(highest_rank, "None")


async def extract_candidate_facts(
    resume_text: str,
    job_requirements: Optional[Dict[str, Any]] = None,
    fail_on_unavailable: bool = True,
    cover_letter_text: Optional[str] = None,
    custom_questions_and_answers: Optional[List[Dict[str, str]]] = None,
) -> Dict[str, Any]:
    """
    LEVEL 2: Grounded Resume Verification

    Single-pass factual extraction from a resume using the enriched schema.
    AI performs extraction only (no evaluation/judgment).
    Returns a dict that is backward-compatible with the existing scoring functions
    while also exposing all new enriched fields.
    """
    _EMPTY_FALLBACK: Dict[str, Any] = {
        "name": "Unknown Candidate",
        "email": "unknown@error.com",
        "phone": "",
        "total_years_experience": 0.0,
        "highest_education_level": "None",
        "job_titles": [],
        "skills": [],
        "skills_with_years": {},
        "skills_detailed": [],
        "skill_matches": [],
        "requires_sponsorship": False,
        "requires_visa_sponsorship": False,
        "extractable_text": False,
        "has_measurable_impact": False,
        "has_contact_info": False,
        "has_clear_job_titles": False,
        "employment_gaps": False,
        "average_tenure_years": 0.0,
        "education": [],
        "jobs": [],
        "cover_letter_analysis": {
            "word_count": 0,
            "mentions_role_title": False,
            "skills_mentioned": [],
            "has_specific_example": False,
            "is_generic": True,
        },
        "custom_answer_analysis": [],
    }

    print("🤖 Sending resume to Gemini for strict fact extraction...")
    await rate_limiter.wait_if_needed()
    model = _get_model()
    if model is None:
        if fail_on_unavailable:
            reason = get_ai_unavailable_reason() or "AI extraction service is unavailable"
            raise AIServiceUnavailableError(reason)
        return dict(_EMPTY_FALLBACK)

    normalized_requirements = normalize_job_requirements(job_requirements)
    required_skills = normalized_requirements.get("must_have_skills", [])

    # --- Build prompt ---
    prompt_parts = [EXTRACTION_SYSTEM_PROMPT, "\n\nRESUME TEXT:\n", resume_text[:12000]]

    if cover_letter_text and cover_letter_text.strip():
        prompt_parts.append("\n\nCOVER LETTER:\n")
        prompt_parts.append(cover_letter_text[:5000])

    if custom_questions_and_answers:
        prompt_parts.append("\n\nCUSTOM APPLICATION QUESTIONS AND ANSWERS:\n")
        for i, qa in enumerate(custom_questions_and_answers):
            q = qa.get("question", "")
            a = qa.get("answer", "")
            prompt_parts.append(f"Question {i}: {q}\nAnswer {i}: {a}\n")

    prompt = "".join(prompt_parts)

    try:
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,
            lambda: model.generate_content(
                prompt,
                generation_config=GenerationConfig(
                    response_mime_type="application/json",
                    response_schema=CANDIDATE_FACT_SCHEMA,
                    temperature=0.0,
                )
            )
        )
        json_text = clean_json_string(response.text)
        raw_extracted = json.loads(json_text)

        # --- Step 1: Validate raw Gemini output ---
        extracted = validate_extraction_result(raw_extracted)

        # --- Step 2: Calculate total years experience from job durations ---
        current_year = _resolve_current_year()

        extracted["total_years_experience"] = calculate_total_years_experience(
            extracted.get("jobs", []), current_year=current_year
        )

        # --- Step 3: Calculate employment gaps from job dates ---
        extracted["employment_gaps"] = calculate_employment_gaps(
            extracted.get("jobs", []), current_year=current_year
        )

        # --- Step 4: Calculate average tenure from job dates ---
        extracted["average_tenure_years"] = calculate_average_tenure(
            extracted.get("jobs", []), current_year=current_year
        )

        # --- Step 5: Calculate years_used per skill from job durations ---
        for skill in extracted.get("skills", []):
            skill["years_used"] = calculate_years_used(
                skill, extracted.get("jobs", []), current_year=current_year
            )

        # --- If resume was not extractable, return early with fallback ---
        if extracted.get("extractable_text") is False:
            fallback = dict(_EMPTY_FALLBACK)
            fallback["name"] = str(extracted.get("name") or "").strip() or "Unknown Candidate"
            email, phone = _validated_contact_fields(
                extracted.get("email"), extracted.get("phone"), resume_text
            )
            fallback["email"] = email
            fallback["phone"] = phone
            fallback["has_contact_info"] = bool(email and email != "unknown@error.com") or bool(phone)
            print("⚠️ Resume not extractable — returning fallback")
            return fallback

        # --- Build backward-compatible fields from enriched extraction ---
        total_years_experience = float(extracted.get("total_years_experience") or 0)

        # Skills: build flat list + skills_with_years dict from new detailed schema
        extracted_skills = extracted.get("skills") or []
        skills: List[str] = []
        skills_with_years: Dict[str, float] = {}
        for item in extracted_skills:
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            skills.append(name)
            try:
                years = float(item.get("years_used") or 1.0)
            except (TypeError, ValueError):
                years = 1.0
            skills_with_years[name] = max(0.5, years)

        # Education: derive highest_education_level from education array
        education = extracted.get("education") or []
        highest_education_level = _derive_highest_education(education)

        # Jobs: derive job_titles from jobs array
        jobs = extracted.get("jobs") or []
        job_titles = [str(j.get("title") or "").strip() for j in jobs if str(j.get("title") or "").strip()]

        # Sponsorship: map to both old field names
        requires_sponsorship = bool(extracted.get("requires_visa_sponsorship", False))

        # Skill matches against required skills (same logic as before)
        extracted_skill_set = {skill.lower() for skill in skills}
        skill_matches = [
            {
                "skill": skill,
                "matched": skill.lower() in extracted_skill_set,
            }
            for skill in required_skills
        ]

        # Prefer LLM-structured fields and only fallback to regex for contact validation.
        name = str(extracted.get("name") or "").strip() or "Unknown Candidate"
        email, phone = _validated_contact_fields(
            extracted.get("email"), extracted.get("phone"), resume_text
        )
        has_contact_info = bool(email and email != "unknown@error.com") or bool(phone)

        # --- Assemble final data dict ---
        data: Dict[str, Any] = {
            # Backward-compatible fields (used by scoring, tasks, knockout filters)
            "name": name,
            "email": email,
            "phone": phone,
            "total_years_experience": total_years_experience,
            "highest_education_level": highest_education_level,
            "job_titles": job_titles,
            "skills": skills,
            "skills_with_years": skills_with_years,
            "skill_matches": skill_matches,
            "requires_sponsorship": requires_sponsorship,
            "requires_visa_sponsorship": requires_sponsorship,
            # New enriched fields
            "extractable_text": extracted.get("extractable_text", True),
            "has_measurable_impact": extracted.get("has_measurable_impact", False),
            "has_contact_info": bool(extracted.get("has_contact_info", False)) or has_contact_info,
            "has_clear_job_titles": extracted.get("has_clear_job_titles", False),
            "employment_gaps": extracted.get("employment_gaps", False),
            "average_tenure_years": extracted.get("average_tenure_years", 0.0),
            "skills_detailed": extracted_skills,
            "education": education,
            "jobs": jobs,
            "cover_letter_analysis": extracted.get("cover_letter_analysis", {
                "word_count": 0,
                "mentions_role_title": False,
                "skills_mentioned": [],
                "has_specific_example": False,
                "is_generic": True,
            }),
            "custom_answer_analysis": extracted.get("custom_answer_analysis", []),
        }

        print("✅ Vertex AI Fact Extraction Successful")
        return data

    except Exception as e:
        # IMPORTANT: do NOT silently return the empty fallback here.
        # The fallback has extractable_text=False, which downstream logic
        # treats as "unreadable resume" → hard knockout → auto-rejection.
        # A transient API error must never reject a candidate. Raise so the
        # Celery task can retry with backoff; callers that explicitly opt
        # out (fail_on_unavailable=False) still get the fallback.
        print(f"❌ VERTEX AI EXTRACTION FAILED: {str(e)}")
        if fail_on_unavailable:
            raise AIServiceUnavailableError(
                f"Resume fact extraction failed: {str(e)}"
            ) from e
        return dict(_EMPTY_FALLBACK)


# --- 4. DETERMINISTIC PYTHON SCORING — no LLM, 100% repeatable ---

def _fuzzy_match_skill(
    required: str, candidate_skills_lower: Dict[str, float]
) -> tuple:
    """
    Returns (matched_key, years) for the best fuzzy match of `required` against
    the candidate's skill dict, or (None, 0.0) if nothing matches.

    Strategy (in priority order):
      1. Exact lowercase match               — "python" == "python"
      2. Whole-term regex match in key       — "java" in "java 17" but not "javascript"
      3. Version suffix match                — "java" matches "java17" / "java-17"
      4. Whole-term reverse containment      — key appears as a whole term in required
    """
    req = required.strip().lower()
    if not req:
        return None, 0.0

    def _whole_term(haystack: str, needle: str) -> bool:
        pattern = rf"(?<![A-Za-z0-9]){re.escape(needle)}(?![A-Za-z0-9])"
        return re.search(pattern, haystack) is not None

    # 1. Exact
    if req in candidate_skills_lower:
        return req, candidate_skills_lower[req]

    # 2. Whole-term regex in candidate keys
    for key, yrs in candidate_skills_lower.items():
        key_norm = key.strip().lower()
        if _whole_term(key_norm, req):
            return key, yrs

        # 3. Version suffix (e.g., java17, java-17, java v17)
        if key_norm.startswith(req):
            suffix = key_norm[len(req):]
            if suffix and re.fullmatch(r"[\s._-]*v?\d+(?:\.\d+)*", suffix):
                return key, yrs

    # 4. Reverse whole-term containment
    for key, yrs in candidate_skills_lower.items():
        key_norm = key.strip().lower()
        if _whole_term(req, key_norm):
            return key, yrs

    return None, 0.0


def calculate_deterministic_score(
    candidate: Dict[str, Any],
    required_skills: List[str],
    min_experience: float,
    job_title: str = "",
    raw_resume_text: str = "",
    required_education: str = "Not specified",
    job_requirements: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Deterministic ATS score with explicit, inspectable math.

    Weights:
      Experience    40 pts
      Skills Match  40 pts
      Education     20 pts
    Total:         100 pts
    """
    normalized_requirements = normalize_job_requirements(job_requirements)
    effective_required_skills = normalized_requirements["must_have_skills"] or required_skills
    effective_min_experience = (
        normalized_requirements["minimum_years_experience"]
        if normalized_requirements["minimum_years_experience"] > 0
        else float(min_experience or 0)
    )
    effective_required_education = (
        normalized_requirements["education_requirement"]
        if normalized_requirements["education_requirement"] != "Not specified"
        else required_education
    )

    skills_with_years: Dict[str, float] = candidate.get("skills_with_years") or {}
    total_years_experience: float = float(candidate.get("total_years_experience") or 0)
    highest_education_level = str(candidate.get("highest_education_level") or "Unknown")
    skill_matches_input: List[Dict[str, Any]] = candidate.get("skill_matches") or []

    # Normalise skill keys to lowercase once
    candidate_skills_lower: Dict[str, float] = {
        k.lower(): v for k, v in skills_with_years.items()
    }
    required_skills_lower: List[str] = [s.lower() for s in effective_required_skills if s]

    # ── 1. Experience Score (40 pts) ─────────────────────────────────────
    exp_denominator = max(effective_min_experience, 1.0)
    exp_ratio = min(1.0, max(0.0, total_years_experience / exp_denominator))
    experience_points = exp_ratio * 40.0

    # ── 2. Skills Match Score (40 pts) ───────────────────────────────────
    matched_skills: List[str] = []

    if skill_matches_input:
        llm_matches_lower = {
            str(item.get("skill", "")).strip().lower(): bool(item.get("matched", False))
            for item in skill_matches_input
            if isinstance(item, dict) and str(item.get("skill", "")).strip()
        }
        for required_skill in required_skills_lower:
            if llm_matches_lower.get(required_skill, False):
                matched_skills.append(required_skill)
    else:
        for required_skill in required_skills_lower:
            matched_key, _ = _fuzzy_match_skill(required_skill, candidate_skills_lower)
            if matched_key is not None:
                matched_skills.append(required_skill)

    matched_skills_count = len(set(matched_skills))
    required_skill_count = len(required_skills_lower)
    skill_ratio = (matched_skills_count / required_skill_count) if required_skill_count else 1.0
    skills_points = skill_ratio * 40.0

    # ── 3. Education Score (20 pts) ──────────────────────────────────────
    education_met = _education_requirement_met(
        highest_education_level=highest_education_level,
        education_requirement=effective_required_education,
    )
    education_points = 20.0 if education_met else 0.0

    # ── Final Score ───────────────────────────────────────────────────────
    raw = experience_points + skills_points + education_points
    final_score = int(round(raw))

    # ── Status Bucketing ─────────────────────────────────────────────────
    if final_score >= 80:
        status = "shortlisted"
    elif final_score >= 60:
        status = "review"
    else:
        status = "rejected"

    summary = (
        f"Experience {total_years_experience:.1f}/{exp_denominator:.1f} years, "
        f"skills matched {matched_skills_count}/{required_skill_count}, "
        f"education requirement met: {'yes' if education_met else 'no'}."
    )

    return {
        "final_score": final_score,
        "status": status,
        "summary": summary,
        "matched_skills_count": matched_skills_count,
        "matched_skills": sorted(set(matched_skills)),
        "required_skills_count": required_skill_count,
        "education_met": education_met,
        "breakdown": {
            "experience": round(experience_points, 1),
            "skills": round(skills_points, 1),
            "education": round(education_points, 1),
        },
    }


def _education_requirement_met(highest_education_level: str, education_requirement: str) -> bool:
    """Return whether extracted education satisfies job requirement."""
    requirement = (education_requirement or "").strip().lower()
    if not requirement or requirement in {"not specified", "none", "n/a"}:
        return True

    ranking = {
        "unknown": 0,
        "high school": 1,
        "associate": 2,
        "bachelor": 3,
        "master": 4,
        "phd": 5,
        "doctorate": 5,
    }

    level_text = (highest_education_level or "unknown").strip().lower()
    candidate_rank = 0
    for key, rank in ranking.items():
        if key in level_text:
            candidate_rank = max(candidate_rank, rank)

    required_rank = 0
    for key, rank in ranking.items():
        if key in requirement:
            required_rank = max(required_rank, rank)

    if required_rank == 0:
        return True

    return candidate_rank >= required_rank


def evaluate_knockout_filters(
    candidate: Dict[str, Any],
    required_skills: List[str],
    min_experience: float,
    job_title: str = "",
    job_requirements: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Evaluate deterministic knockout conditions and return status metadata."""
    normalized_requirements = normalize_job_requirements(job_requirements)
    effective_required_skills = normalized_requirements["must_have_skills"] or required_skills
    effective_min_experience = (
        normalized_requirements["minimum_years_experience"]
        if normalized_requirements["minimum_years_experience"] > 0
        else float(min_experience or 0)
    )

    total_years_experience = float(candidate.get("total_years_experience") or 0.0)
    required_skills_lower = [s.lower() for s in effective_required_skills if s]

    skill_matches = candidate.get("skill_matches") or []
    candidate_skills_lower = {
        str(k).lower(): float(v) for k, v in (candidate.get("skills_with_years") or {}).items()
    }

    matched_skills_count = 0
    if skill_matches:
        llm_matches_lower = {
            str(item.get("skill", "")).strip().lower(): bool(item.get("matched", False))
            for item in skill_matches
            if isinstance(item, dict) and str(item.get("skill", "")).strip()
        }
        matched_skills_count = sum(1 for s in required_skills_lower if llm_matches_lower.get(s, False))
    else:
        for skill in required_skills_lower:
            key, _ = _fuzzy_match_skill(skill, candidate_skills_lower)
            if key is not None:
                matched_skills_count += 1

    if effective_min_experience > 0:
        exp_ratio = total_years_experience / max(effective_min_experience, 1.0)
    else:
        exp_ratio = 1.0

    skill_ratio = (
        matched_skills_count / len(required_skills_lower)
        if required_skills_lower
        else 1.0
    )

    reasons: List[str] = []

    if "senior" in (job_title or "").lower() and total_years_experience <= 0:
        reasons.append("0 years of experience for senior role")

    requires_visa = candidate.get("requires_sponsorship")
    if requires_visa is None:
        requires_visa = candidate.get("requires_visa_sponsorship")
    offers_visa = normalized_requirements.get("offers_visa_sponsorship")
    if requires_visa is True and offers_visa is False:
        reasons.append("candidate requires visa sponsorship but role does not offer it")

    knockout = bool(reasons)
    reason = "; ".join(reasons)
    return {
        "knockout": knockout,
        "reason": reason,
        "experience_ratio": max(0.0, min(1.0, exp_ratio)),
        "skills_ratio": max(0.0, min(1.0, skill_ratio)),
        "matched_skills_count": matched_skills_count,
        "required_skills_count": len(required_skills_lower),
    }


# --- 5. CANDIDATE SUMMARY GENERATION ---

SUMMARY_SYSTEM_PROMPT = """You are a recruitment analyst writing internal notes for a hiring team. Your job is to produce a structured JSON summary of a candidate based on extracted resume data and scoring results.

Rules you must follow without exception:
1. Write only what the data supports — never invent or assume facts not present in the provided data.
2. Never use subjective language like "impressive", "strong background", "excellent", "talented", "ideal" — describe facts only.
3. Never make a hiring recommendation. Never say "should hire" or "should reject".
4. Never compare this candidate to other candidates.
5. The candidate_summary must be based only on the extracted facts provided, not on any prior knowledge. It must NEVER be null — if data is limited, write what you can from available facts and note naturally in the text what information is missing.
6. For candidates in the "Filtered Out" bucket, match_reasoning must reference ONLY the hard gate failures listed in the knockout_flags array. Do NOT mention skill gaps, scoring penalties, or any other scoring details — only the knockout reasons.
7. Return only a valid JSON object with exactly the keys specified. No preamble, no markdown, no code fences."""


def _calculate_extraction_confidence(candidate_data: Dict[str, Any]) -> Dict[str, Any]:
    """Deterministically calculate extraction confidence from candidate data."""
    jobs = candidate_data.get("jobs") or []
    skills = candidate_data.get("skills_detailed") or []
    education = candidate_data.get("education") or []
    extractable = candidate_data.get("extractable_text", True)

    jobs_with_start = [j for j in jobs if j.get("start_year") is not None]
    all_null_starts = len(jobs) > 0 and len(jobs_with_start) == 0

    # Low confidence checks
    if extractable is False:
        return {"level": "low", "confidence_reason": "Resume could not be parsed into readable text."}
    if len(jobs) < 2:
        return {"level": "low", "confidence_reason": "Fewer than 2 work history entries were extracted."}
    if len(skills) < 2:
        return {"level": "low", "confidence_reason": "Fewer than 2 skills were identified in the resume."}
    if all_null_starts:
        return {"level": "low", "confidence_reason": "No job entries included start dates."}

    # High confidence checks — all must be true
    has_clear_titles = candidate_data.get("has_clear_job_titles", False)
    has_impact = candidate_data.get("has_measurable_impact", False)
    if (
        len(jobs_with_start) >= 2
        and len(skills) >= 3
        and len(education) >= 1
        and has_clear_titles
        and has_impact
    ):
        return {"level": "high"}

    # Everything else is medium
    reasons = []
    if len(jobs_with_start) < 2:
        reasons.append("some job entries lack start dates")
    if len(skills) < 3:
        reasons.append("limited skills identified")
    if not education:
        reasons.append("no education entries found")
    if not has_clear_titles:
        reasons.append("some roles lack clear job titles")
    if not has_impact:
        reasons.append("no quantified achievements found")
    reason = "; ".join(reasons) if reasons else "Some resume details were incomplete."
    return {"level": "medium", "confidence_reason": reason}


def _build_summary_fallback(
    bucket: str,
    total_score: int,
    confidence: Dict[str, Any],
    candidate_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build a fallback summary when Gemini is unavailable or fails."""
    # Build a non-null candidate_summary from whatever data is available
    parts = []
    if candidate_data:
        name = candidate_data.get("name")
        if name and name != "Unknown Candidate":
            parts.append(name)
        titles = candidate_data.get("job_titles") or []
        if titles:
            parts.append(f"currently {titles[0]}" if len(parts) else titles[0])
        years = candidate_data.get("total_years_experience")
        if years and years > 0:
            parts.append(f"with {years:.0f} years of experience")
        edu = candidate_data.get("highest_education_level")
        if edu and edu.lower() not in ("none", "unknown", ""):
            parts.append(f"holding a {edu} degree")
    candidate_summary = ". ".join([" ".join(parts) + "."]) if parts else "Insufficient data to generate a candidate summary."

    fallback: Dict[str, Any] = {
        "candidate_summary": candidate_summary,
        "match_reasoning": f"Candidate was assigned to {bucket} with a score of {total_score}/100.",
        "override_suggestion": None if bucket == "Strong Match" else "Review the resume manually to verify the system's assessment.",
        "extraction_confidence": confidence["level"],
    }
    if "confidence_reason" in confidence:
        fallback["confidence_reason"] = confidence["confidence_reason"]
    return fallback


async def generate_candidate_summary(
    candidate_data: Dict[str, Any],
    score_breakdown: Dict[str, Any],
    bucket: str,
    knockout_flags: List[Dict[str, Any]],
    job_config: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Generate a structured natural language summary for a candidate using Gemini.

    Returns a dict with keys: candidate_summary, match_reasoning,
    override_suggestion, extraction_confidence, and optionally confidence_reason.
    """
    # Calculate confidence deterministically before calling Gemini
    confidence = _calculate_extraction_confidence(candidate_data)
    total_score = sum(
        v.get("total", 0) for k, v in score_breakdown.items()
        if isinstance(v, dict) and v.get("affects_score", False)
    )

    model = _get_model()
    if model is None:
        logger.warning("Gemini unavailable for summary generation — using fallback")
        return _build_summary_fallback(bucket, total_score, confidence, candidate_data)

    await rate_limiter.wait_if_needed()

    # Build structured user message with all context Gemini needs
    user_data = {
        "candidate": {
            "name": candidate_data.get("name"),
            "total_years_experience": candidate_data.get("total_years_experience"),
            "highest_education_level": candidate_data.get("highest_education_level"),
            "job_titles": candidate_data.get("job_titles", []),
            "jobs": candidate_data.get("jobs", []),
            "education": candidate_data.get("education", []),
            "skills": [s.get("name") for s in (candidate_data.get("skills_detailed") or []) if s.get("name")],
            "has_measurable_impact": candidate_data.get("has_measurable_impact"),
            "employment_gaps": candidate_data.get("employment_gaps"),
            "average_tenure_years": candidate_data.get("average_tenure_years"),
        },
        "job_requirements": {
            "title": job_config.get("title"),
            "required_skills": job_config.get("required_skills", []),
            "min_experience": job_config.get("min_experience"),
            "required_education": job_config.get("required_education"),
            "department": job_config.get("department"),
        },
        "scoring": {
            "total_score": total_score,
            "bucket": bucket,
            "score_breakdown": score_breakdown,
        },
        "knockout_flags": knockout_flags,
    }
    if "confidence_reason" in confidence:
        user_data["extraction_confidence_reason"] = confidence["confidence_reason"]

    override_instruction = ""
    if bucket == "Strong Match":
        override_instruction = 'Set "override_suggestion" to null.'
    elif bucket == "Filtered Out":
        override_instruction = '"override_suggestion" should tell the recruiter what to check before confirming the rejection.'
    else:
        override_instruction = '"override_suggestion" should tell the recruiter what could move this candidate to Strong Match.'

    confidence_note = ""
    if "confidence_reason" in confidence:
        confidence_note = f'\nThe extraction confidence is {confidence["level"]}. Reason: {confidence["confidence_reason"]}. If data is limited, note naturally in the candidate_summary what information was not found in the resume.'

    prompt = f"""{SUMMARY_SYSTEM_PROMPT}

Produce a JSON object with exactly these keys:
- "candidate_summary": 2-3 factual sentences about who this candidate is based on the data below. Cover their current role, total years of experience, industry background, and education. This field must NEVER be null — write what you can from the available data.
- "match_reasoning": 2-3 sentences explaining why the candidate was placed in the "{bucket}" bucket. {'For this Filtered Out candidate, reference ONLY the hard gate failures from the knockout_flags list. Do not mention skill gaps or scoring.' if bucket == 'Filtered Out' else 'Reference actual job requirements and how they were met or missed.'}
- "override_suggestion": {override_instruction}
{confidence_note}
DATA:
{json.dumps(user_data, default=str)}

Return ONLY the JSON object."""

    try:
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,
            lambda: model.generate_content(
                prompt,
                generation_config=GenerationConfig(
                    temperature=0.1,
                    response_mime_type="application/json",
                )
            )
        )
        json_text = clean_json_string(response.text)
        parsed = json.loads(json_text)

        # Validate required fields exist
        result: Dict[str, Any] = {
            "candidate_summary": parsed.get("candidate_summary"),
            "match_reasoning": parsed.get("match_reasoning"),
            "override_suggestion": parsed.get("override_suggestion") if bucket != "Strong Match" else None,
            "extraction_confidence": confidence["level"],
        }
        if "confidence_reason" in confidence:
            result["confidence_reason"] = confidence["confidence_reason"]

        # Ensure no null in required text fields (except override_suggestion for Strong Match)
        if not result["candidate_summary"] or not result["match_reasoning"]:
            logger.warning("Gemini returned incomplete summary fields — using fallback")
            return _build_summary_fallback(bucket, total_score, confidence, candidate_data)

        return result

    except Exception as e:
        logger.error(f"Summary generation failed: {e}")
        return _build_summary_fallback(bucket, total_score, confidence, candidate_data)


def generate_candidate_summary_sync(
    candidate_data: Dict[str, Any],
    score_breakdown: Dict[str, Any],
    bucket: str,
    knockout_flags: List[Dict[str, Any]],
    job_config: Dict[str, Any],
) -> Dict[str, Any]:
    """Synchronous wrapper around generate_candidate_summary."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(generate_candidate_summary(
            candidate_data, score_breakdown, bucket, knockout_flags, job_config,
        ))

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(lambda: asyncio.run(generate_candidate_summary(
            candidate_data, score_breakdown, bucket, knockout_flags, job_config,
        )))
        return future.result()


async def get_skill_embeddings(skill_names: List[str]) -> Dict[str, List[float]]:
    """
    Generate text embeddings for skill names using Vertex AI text-embedding-004.

    Returns a dict of {skill_name: embedding_values}. If any embedding API call
    fails, logs the error and returns an empty dict so callers can safely fall
    back to alias-only matching.
    """
    if not skill_names:
        return {}

    embedding_model = _get_embedding_model()
    if embedding_model is None:
        logger.warning("Skill embedding model unavailable; returning empty embedding map")
        return {}

    # Keep insertion order stable and avoid duplicate API payload entries.
    unique_skills: List[str] = []
    seen: set[str] = set()
    for raw_name in skill_names:
        if not isinstance(raw_name, str):
            continue
        skill_name = raw_name.strip()
        if not skill_name:
            continue
        if skill_name in seen:
            continue
        seen.add(skill_name)
        unique_skills.append(skill_name)

    if not unique_skills:
        return {}

    embeddings: Dict[str, List[float]] = {}
    loop = asyncio.get_event_loop()

    for start_idx in range(0, len(unique_skills), EMBEDDING_BATCH_SIZE):
        batch = unique_skills[start_idx : start_idx + EMBEDDING_BATCH_SIZE]
        batch_phrases = [f"{SKILL_EMBEDDING_PREFIX}{skill_name}" for skill_name in batch]
        try:
            await rate_limiter.wait_if_needed()
            batch_embeddings = await loop.run_in_executor(
                None,
                lambda payload=batch_phrases: embedding_model.get_embeddings(payload),
            )
            for skill_name, embedding in zip(batch, batch_embeddings):
                values = getattr(embedding, "values", None)
                if values is None:
                    continue
                embeddings[skill_name] = list(values)
        except Exception as exc:
            logger.error(
                "Skill embedding generation failed for batch %s-%s: %s",
                start_idx,
                start_idx + len(batch) - 1,
                exc,
            )
            return {}

    return embeddings


def get_skill_embeddings_sync(skill_names: List[str]) -> Dict[str, List[float]]:
    """Synchronous wrapper around get_skill_embeddings."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(get_skill_embeddings(skill_names))

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(lambda: asyncio.run(get_skill_embeddings(skill_names)))
        return future.result()


# --- 6. SYNC WRAPPERS (for single-resume endpoints that can't be async) ---

def extract_candidate_facts_sync(
    resume_text: str,
    cover_letter_text: Optional[str] = None,
    custom_questions_and_answers: Optional[List[Dict[str, str]]] = None,
) -> Dict[str, Any]:
    """Synchronous wrapper around extract_candidate_facts."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(extract_candidate_facts(
            resume_text,
            cover_letter_text=cover_letter_text,
            custom_questions_and_answers=custom_questions_and_answers,
        ))

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(lambda: asyncio.run(extract_candidate_facts(
            resume_text,
            cover_letter_text=cover_letter_text,
            custom_questions_and_answers=custom_questions_and_answers,
        )))
        return future.result()


# ==============================================================================
# VERIFICATION TEST OUTPUTS — March 4, 2026 (Updated after Python-calculated fields fix)
# Run via test_extraction_verification.py against 3 test resumes.
# years_used, employment_gaps, and average_tenure_years are now calculated
# in Python after Gemini extraction, not by Gemini itself.
# ==============================================================================
#
# --- TEST CASE 1: Strong resume (clear dates, many skills, degree) ---
# {
#   "name": "Unknown Candidate",
#   "email": "jane.smith@example.com",
#   "phone": "555) 123-4567",
#   "total_years_experience": 10.0,
#   "highest_education_level": "Masters",
#   "job_titles": ["Senior Software Engineer", "Software Engineer", "Junior Developer"],
#   "skills": ["python", "go", "kubernetes", "postgresql", "redis", "kafka", "aws",
#     "ruby", "react", "typescript", "mysql", "docker", "graphql", "java",
#     "spring boot", "oracle db", "angular", "ruby on rails", "javascript", "git",
#     "ci/cd", "terraform", "gcp"],
#   "skills_with_years": {"python": 5.0, "go": 5.0, "kubernetes": 5.0, "postgresql": 5.0,
#     "redis": 5.0, "kafka": 5.0, "aws": 5.0, "ruby": 3.0, "react": 3.0,
#     "typescript": 3.0, "mysql": 3.0, "docker": 3.0, "graphql": 3.0, "java": 3.0,
#     "spring boot": 3.0, "oracle db": 3.0, "angular": 3.0, "ruby on rails": 3.0,
#     "javascript": 1.0, "git": 1.0, "ci/cd": 1.0, "terraform": 1.0, "gcp": 1.0},
#   "skill_matches": [
#     {"skill": "Python", "matched": true},
#     {"skill": "React", "matched": true},
#     {"skill": "AWS", "matched": true}
#   ],
#   "requires_sponsorship": false,
#   "requires_visa_sponsorship": false,
#   "extractable_text": true,
#   "has_measurable_impact": true,
#   "has_contact_info": true,
#   "has_clear_job_titles": true,
#   "employment_gaps": true,
#   "average_tenure_years": 3.7,
#   "skills_detailed": [
#     {"name": "python", "last_used_year": 2023, "job_index": 0, "years_used": 5},
#     {"name": "go", "last_used_year": 2023, "job_index": 0, "years_used": 5},
#     {"name": "kubernetes", "last_used_year": 2023, "job_index": 0, "years_used": 5},
#     {"name": "postgresql", "last_used_year": 2023, "job_index": 0, "years_used": 5},
#     {"name": "redis", "last_used_year": 2023, "job_index": 0, "years_used": 5},
#     {"name": "kafka", "last_used_year": 2023, "job_index": 0, "years_used": 5},
#     {"name": "aws", "last_used_year": 2023, "job_index": 0, "years_used": 5},
#     {"name": "ruby", "last_used_year": 2020, "job_index": 1, "years_used": 3},
#     {"name": "react", "last_used_year": 2020, "job_index": 1, "years_used": 3},
#     {"name": "typescript", "last_used_year": 2020, "job_index": 1, "years_used": 3},
#     {"name": "mysql", "last_used_year": 2020, "job_index": 1, "years_used": 3},
#     {"name": "docker", "last_used_year": 2020, "job_index": 1, "years_used": 3},
#     {"name": "graphql", "last_used_year": 2020, "job_index": 1, "years_used": 3},
#     {"name": "java", "last_used_year": 2017, "job_index": 2, "years_used": 3},
#     {"name": "spring boot", "last_used_year": 2017, "job_index": 2, "years_used": 3},
#     {"name": "oracle db", "last_used_year": 2017, "job_index": 2, "years_used": 3},
#     {"name": "angular", "last_used_year": 2017, "job_index": 2, "years_used": 3},
#     {"name": "ruby on rails", "last_used_year": 2020, "job_index": 1, "years_used": 3},
#     {"name": "javascript", "last_used_year": null, "job_index": null, "years_used": null},
#     {"name": "git", "last_used_year": null, "job_index": null, "years_used": null},
#     {"name": "ci/cd", "last_used_year": null, "job_index": null, "years_used": null},
#     {"name": "terraform", "last_used_year": null, "job_index": null, "years_used": null},
#     {"name": "gcp", "last_used_year": null, "job_index": null, "years_used": null}
#   ],
#   "education": [
#     {"degree": "master", "field_of_study": "computer science", "institution": "stanford university", "year": 2014},
#     {"degree": "bachelor", "field_of_study": "mathematics", "institution": "uc berkeley", "year": 2012}
#   ],
#   "jobs": [
#     {"title": "Senior Software Engineer", "company": "Stripe", "start_year": 2021,
#      "end_year": null, "is_current": true, "domain": "fintech", "work_type": "remote"},
#     {"title": "Software Engineer", "company": "Shopify", "start_year": 2017,
#      "end_year": 2020, "is_current": false, "domain": "ecommerce", "work_type": "unknown"},
#     {"title": "Junior Developer", "company": "Accenture", "start_year": 2014,
#      "end_year": 2017, "is_current": false, "domain": "enterprise", "work_type": "unknown"}
#   ],
#   "cover_letter_analysis": {
#     "word_count": 0, "mentions_role_title": false, "skills_mentioned": [],
#     "has_specific_example": false, "is_generic": false
#   },
#   "custom_answer_analysis": []
# }
#
# --- TEST CASE 2: Employment gaps, no degree ---
# {
#   "name": "Unknown Candidate",
#   "email": "mike.j@gmail.com",
#   "phone": "",
#   "total_years_experience": 6.0,
#   "highest_education_level": "None",
#   "job_titles": ["Freelance Web Developer", "Data Entry Clerk", "Warehouse Associate", "Barista"],
#   "skills": ["html", "css", "javascript", "php", "wordpress", "microsoft excel", "microsoft access"],
#   "skills_with_years": {"html": 2.0, "css": 2.0, "javascript": 2.0, "php": 2.0,
#     "wordpress": 2.0, "microsoft excel": 2.0, "microsoft access": 2.0},
#   "skill_matches": [
#     {"skill": "Python", "matched": false},
#     {"skill": "React", "matched": false},
#     {"skill": "AWS", "matched": false}
#   ],
#   "requires_sponsorship": false,
#   "requires_visa_sponsorship": false,
#   "extractable_text": true,
#   "has_measurable_impact": true,
#   "has_contact_info": true,
#   "has_clear_job_titles": true,
#   "employment_gaps": true,
#   "average_tenure_years": 1.5,
#   "skills_detailed": [
#     {"name": "html", "last_used_year": 2024, "job_index": 0, "years_used": 2},
#     {"name": "css", "last_used_year": 2024, "job_index": 0, "years_used": 2},
#     {"name": "javascript", "last_used_year": 2024, "job_index": 0, "years_used": 2},
#     {"name": "php", "last_used_year": 2024, "job_index": 0, "years_used": 2},
#     {"name": "wordpress", "last_used_year": 2024, "job_index": 0, "years_used": 2},
#     {"name": "microsoft excel", "last_used_year": 2022, "job_index": 1, "years_used": 2},
#     {"name": "microsoft access", "last_used_year": 2022, "job_index": 1, "years_used": 2}
#   ],
#   "education": [],
#   "jobs": [
#     {"title": "Freelance Web Developer", "company": "Freelance", "start_year": 2024,
#      "end_year": null, "is_current": true, "domain": "other", "work_type": "unknown"},
#     {"title": "Data Entry Clerk", "company": "OfficeMax", "start_year": 2020,
#      "end_year": 2022, "is_current": false, "domain": "other", "work_type": "unknown"},
#     {"title": "Warehouse Associate", "company": "Amazon", "start_year": 2018,
#      "end_year": 2018, "is_current": false, "domain": "logistics", "work_type": "unknown"},
#     {"title": "Barista", "company": "Starbucks", "start_year": 2015,
#      "end_year": 2017, "is_current": false, "domain": "other", "work_type": "unknown"}
#   ],
#   "cover_letter_analysis": {
#     "word_count": 0, "mentions_role_title": false, "skills_mentioned": [],
#     "has_specific_example": false, "is_generic": false
#   },
#   "custom_answer_analysis": []
# }
#
# --- TEST CASE 3: Minimal / poorly formatted resume ---
# {
#   "name": "Unknown Candidate",
#   "email": "alexthompson99@hotmail.com",
#   "phone": "2019-2021",
#   "total_years_experience": 2.0,
#   "highest_education_level": "None",
#   "job_titles": ["doing stuff"],
#   "skills": ["python", "excel"],
#   "skills_with_years": {"python": 2.0, "excel": 2.0},
#   "skill_matches": [
#     {"skill": "Python", "matched": true},
#     {"skill": "React", "matched": false},
#     {"skill": "AWS", "matched": false}
#   ],
#   "requires_sponsorship": false,
#   "requires_visa_sponsorship": false,
#   "extractable_text": true,
#   "has_measurable_impact": false,
#   "has_contact_info": true,
#   "has_clear_job_titles": true,
#   "employment_gaps": false,
#   "average_tenure_years": 2.0,
#   "skills_detailed": [
#     {"name": "python", "last_used_year": 2021, "job_index": 0, "years_used": 2},
#     {"name": "excel", "last_used_year": 2021, "job_index": 0, "years_used": 2}
#   ],
#   "education": [
#     {"degree": "none", "field_of_study": null, "institution": null, "year": null}
#   ],
#   "jobs": [
#     {"title": "doing stuff", "company": "some company", "start_year": 2019,
#      "end_year": 2021, "is_current": false, "domain": "other", "work_type": "unknown"}
#   ],
#   "cover_letter_analysis": {
#     "word_count": 0, "mentions_role_title": false, "skills_mentioned": [],
#     "has_specific_example": false, "is_generic": false
#   },
#   "custom_answer_analysis": []
# }
