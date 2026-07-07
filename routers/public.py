"""
Public-facing unauthenticated endpoints for the Careers Portal.

These endpoints do NOT require authentication and are accessible to candidates
applying to jobs through public links.
"""

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Request
from sqlalchemy.orm import Session
from typing import Optional
from collections import defaultdict, deque
from datetime import date
import threading
import time
import base64
import models
import schemas
from database import get_db
from services.ai_engine import is_ai_available
from services.tasks import process_public_resume
import json

router = APIRouter(prefix="/api/public", tags=["public"])

PUBLIC_APPLY_WINDOW_SECONDS = 60
PUBLIC_APPLY_LIMIT_PER_IP = 20
PUBLIC_APPLY_LIMIT_PER_EMAIL = 5
_public_apply_rate_lock = threading.Lock()
_public_apply_ip_windows: dict[str, deque[float]] = defaultdict(deque)
_public_apply_email_windows: dict[str, deque[float]] = defaultdict(deque)


def _is_rate_limited(bucket: dict[str, deque[float]], key: str, limit: int, window_seconds: int) -> bool:
    now = time.time()
    with _public_apply_rate_lock:
        samples = bucket[key]
        while samples and (now - samples[0]) > window_seconds:
            samples.popleft()
        if len(samples) >= limit:
            return True
        samples.append(now)
        return False


# ==============================================================================
# PUBLIC JOB VIEW ENDPOINT
# ==============================================================================

@router.get("/job/{job_id}", response_model=schemas.PublicJobResponse)
def get_public_job(job_id: int, db: Session = Depends(get_db)):
    """
    Fetch public job details and increment view counter.
    
    Returns 404 if job is not found or inactive.
    No authentication required.
    """
    job = db.query(models.Job).filter(models.Job.id == job_id).first()
    
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    if not job.is_active:
        raise HTTPException(status_code=404, detail="This job posting is no longer active")

    if (job.visibility or "Public") == "Internal":
        raise HTTPException(status_code=403, detail="This position is not publicly listed")
    
    # Increment view counter
    job.views = (job.views or 0) + 1
    db.commit()
    
    return job


@router.post("/apply/{job_id}")
async def submit_public_application(
    job_id: int,
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
    phone: Optional[str] = Form(None),
    linkedin_url: Optional[str] = Form(None),
    portfolio_url: Optional[str] = Form(None),
    cover_letter: Optional[str] = Form(None),
    custom_answers_json: Optional[str] = Form(None),
    resume: UploadFile = File(...),
    website_url_catch: Optional[str] = Form(None),  # Honeypot field for bot detection
    db: Session = Depends(get_db)
):
    """
    Submit a public job application.
    
    Accepts multipart/form-data with:
    - Candidate info (name, email, phone)
    - Optional fields (linkedin_url, portfolio_url)
    - Custom answers (JSON string)
    - Resume PDF file
    - Honeypot field (website_url_catch) for spam protection
    
    Returns 200 OK immediately and processes in background.
    No authentication required.
    """
    
    # Spam Protection: Honeypot check
    # If the hidden field is filled, silently discard (bots typically fill all fields)
    if website_url_catch and website_url_catch.strip():
        print(f"🤖 Bot detected via honeypot: {email}")
        return {"status": "success", "message": "Application received"}

    if not is_ai_available():
        raise HTTPException(
            status_code=503,
            detail=(
                "Application processing is temporarily unavailable. "
                "Please try again shortly."
            ),
        )

    client_ip = request.client.host if request.client else "unknown"
    normalized_email = (email or "").strip().lower()

    if _is_rate_limited(_public_apply_ip_windows, client_ip, PUBLIC_APPLY_LIMIT_PER_IP, PUBLIC_APPLY_WINDOW_SECONDS):
        raise HTTPException(status_code=429, detail="Too many submissions from this IP. Please retry shortly.")
    if normalized_email and _is_rate_limited(_public_apply_email_windows, normalized_email, PUBLIC_APPLY_LIMIT_PER_EMAIL, PUBLIC_APPLY_WINDOW_SECONDS):
        raise HTTPException(status_code=429, detail="Too many submissions for this email. Please retry shortly.")
    
    # Fetch job and validate
    job = db.query(models.Job).filter(models.Job.id == job_id).first()
    
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    if not job.is_active:
        raise HTTPException(status_code=400, detail="This job posting is no longer active")

    if (job.visibility or "Public") == "Internal":
        raise HTTPException(status_code=403, detail="This position is not publicly listed")

    if job.application_deadline and date.today() > job.application_deadline:
        raise HTTPException(
            status_code=400,
            detail="Applications for this position are no longer being accepted",
        )
    
    # Validate file type
    if not resume.filename.lower().endswith('.pdf'):
        raise HTTPException(status_code=400, detail="Only PDF resumes are accepted")
    
    if not resume.size and not resume.filename:
        raise HTTPException(status_code=400, detail="Resume file is required")

    if resume.size and resume.size > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Resume file too large (max 10MB)")
    
    # Parse custom answers JSON if provided
    custom_answers = None
    if custom_answers_json:
        try:
            custom_answers = json.loads(custom_answers_json)
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="Invalid custom_answers_json payload")

    normalized_linkedin = (linkedin_url or "").strip()
    normalized_portfolio = (portfolio_url or "").strip()
    normalized_cover_letter = (cover_letter or "").strip()

    if job.require_linkedin and not normalized_linkedin:
        raise HTTPException(status_code=400, detail="LinkedIn URL is required for this position")

    if job.require_portfolio and not normalized_portfolio:
        raise HTTPException(status_code=400, detail="Portfolio URL is required for this position")

    if job.require_cover_letter and not normalized_cover_letter:
        raise HTTPException(status_code=400, detail="Cover letter is required for this position")

    expected_questions = [q.strip() for q in (job.custom_questions or []) if str(q).strip()]
    normalized_answers = None
    if expected_questions:
        if not isinstance(custom_answers, list):
            raise HTTPException(status_code=400, detail="All custom questions must be answered")

        answer_map: dict[str, str] = {}
        for item in custom_answers:
            if not isinstance(item, dict):
                continue
            question = str(item.get("question") or "").strip()
            answer = str(item.get("answer") or "").strip()
            if question:
                answer_map[question] = answer

        missing = [q for q in expected_questions if not answer_map.get(q)]
        if missing:
            raise HTTPException(status_code=400, detail="All custom questions must be answered")

        normalized_answers = [{"question": q, "answer": answer_map[q]} for q in expected_questions]
    elif custom_answers is not None:
        # Allow optional custom answers only as a well-formed list of objects.
        if not isinstance(custom_answers, list):
            raise HTTPException(status_code=400, detail="Invalid custom_answers_json payload")
        normalized_answers = []
        for item in custom_answers:
            if not isinstance(item, dict):
                raise HTTPException(status_code=400, detail="Invalid custom_answers_json payload")
            question = str(item.get("question") or "").strip()
            answer = str(item.get("answer") or "").strip()
            if question or answer:
                if not question or not answer:
                    raise HTTPException(status_code=400, detail="Invalid custom_answers_json payload")
                normalized_answers.append({"question": question, "answer": answer})

    duplicate = (
        db.query(models.Applicant)
        .filter(models.Applicant.job_id == job.id, models.Applicant.email == normalized_email)
        .first()
    )
    if duplicate:
        # Return silent success — don't reveal to the applicant (or scrapers) that
        # a prior application exists.  The Celery task also guards via unique constraint.
        return {"status": "success", "message": "Application received and being processed"}

    resume_bytes = await resume.read()
    if not resume_bytes:
        raise HTTPException(status_code=400, detail="Resume file is required")
    if len(resume_bytes) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Resume file too large (max 10MB)")

    resume_b64 = base64.b64encode(resume_bytes).decode("ascii")

    process_public_resume.delay(
        resume_b64=resume_b64,
        job_id=job.id,
        company_id=job.company_id,
        submitted_name=name,
        submitted_email=normalized_email,
        submitted_phone=phone,
        linkedin_url=normalized_linkedin or None,
        portfolio_url=normalized_portfolio or None,
        cover_letter=normalized_cover_letter or None,
        custom_answers=normalized_answers,
    )
    
    return {
        "status": "success",
        "message": "Application received and being processed"
    }
