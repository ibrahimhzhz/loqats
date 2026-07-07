import asyncio
import re
import base64
import json
import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

import models
from core.celery_app import celery_app
from database import SessionLocal
from services.ai_engine import extract_candidate_facts, extract_jd_requirements
from services.ai_engine import normalize_job_requirements, merge_required_skills
from services.ai_engine import generate_candidate_summary_sync
from services.ai_engine import get_skill_embeddings_sync
from services.pdf_parser import extract_text_from_pdf
from services import storage
from services import email as email_service
from scoring import (
    calculate_deterministic_score,
    evaluate_knockout_filters,
    generate_candidate_signals,
    assign_bucket,
    bucket_to_status,
)


def _serialize_summary(summary_payload: Any) -> str:
    """Store structured AI summaries as JSON text in Applicant.summary."""
    if summary_payload is None:
        return ""
    if isinstance(summary_payload, str):
        return summary_payload
    if isinstance(summary_payload, (dict, list)):
        try:
            return json.dumps(summary_payload)
        except (TypeError, ValueError):
            return str(summary_payload)
    return str(summary_payload)


def _extract_skill_names_for_embeddings(skills_detailed: List[Dict[str, Any]] | None) -> List[str]:
    """Return de-duplicated skill names from extracted skills_detailed payload."""
    names: List[str] = []
    seen: set[str] = set()

    for skill in (skills_detailed or []):
        raw_name = (skill or {}).get("name")
        if not raw_name:
            continue
        skill_name = str(raw_name).strip()
        if not skill_name:
            continue
        dedupe_key = skill_name.lower()
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        names.append(skill_name)

    return names


def _get_or_create_required_skill_embeddings(
    db,
    job: models.Job,
    required_skill_names: List[str],
) -> Dict[str, List[float]]:
    """
    Return cached required skill embeddings for a job, generating once when absent.
    """
    if job.required_skill_embeddings is not None:
        if isinstance(job.required_skill_embeddings, dict):
            logger.info(
                "Reusing required skill embeddings for job_id=%s (%s skills)",
                job.id,
                len(job.required_skill_embeddings),
            )
            return job.required_skill_embeddings

        logger.warning(
            "Job %s has non-dict required_skill_embeddings; treating as empty map",
            job.id,
        )
        return {}

    if not required_skill_names:
        job.required_skill_embeddings = {}
        db.flush()
        return {}

    generated = get_skill_embeddings_sync(required_skill_names)
    if generated:
        logger.info(
            "Generated required skill embeddings for job_id=%s (%s skills)",
            job.id,
            len(generated),
        )
    else:
        logger.error(
            "Failed generating required skill embeddings for job_id=%s; storing empty map",
            job.id,
        )

    job.required_skill_embeddings = generated or {}
    db.flush()
    return job.required_skill_embeddings


def _advance_job_progress(db, job_id: int, company_id: int):
    db.query(models.Job).filter(
        models.Job.id == job_id,
        models.Job.company_id == company_id,
    ).update(
        {
            "processed_resumes": func.coalesce(models.Job.processed_resumes, 0) + 1,
            "status": "processing",
        }
    )
    db.commit()


def _build_results_payload(job: models.Job, applicants: List[models.Applicant]) -> Dict[str, Any]:
    candidates: List[Dict[str, Any]] = []
    for applicant in applicants:
        candidates.append(
            {
                "id": applicant.id,
                "name": applicant.name,
                "email": applicant.email,
                "years_experience": applicant.years_experience,
                "skills": applicant.skills,
                "match_score": applicant.match_score,
                "summary": applicant.summary,
                "status": applicant.status,
                "breakdown": applicant.breakdown,
            }
        )

    candidates.sort(
        key=lambda x: (
            1 if x.get("status") == "knockout" else 0,
            -(x.get("match_score") or 0),
        )
    )

    # Shortlist = candidates whose status is "shortlisted" (score >= bucket
    # threshold). Previously this was "top 10% by score", which contradicted
    # the per-candidate status shown elsewhere in the UI — the same person
    # could be labelled shortlisted on their card but absent from the
    # shortlist panel, or vice versa.
    shortlisted = [c for c in candidates if c.get("status") == "shortlisted"]

    processed_resumes = int(job.processed_resumes or 0)
    total_saved = len(candidates)
    rejected_count = sum(1 for c in candidates if c.get("status") == "rejected")
    review_count = sum(1 for c in candidates if c.get("status") == "review")
    knockout_count = sum(1 for c in candidates if c.get("status") == "knockout")

    duplicates_or_skipped = max(0, processed_resumes - total_saved)

    return {
        "total_processed": total_saved,
        "duplicates_skipped": duplicates_or_skipped,
        "knocked_out": knockout_count,
        "scored": total_saved,
        "ai_evaluated": total_saved,
        "shortlisted_count": len(shortlisted),
        "review_count": review_count,
        "rejected_count": rejected_count,
        "knockout_count": knockout_count,
        "shortlisted": shortlisted,
        "all_candidates": candidates,
        "criteria": {
            "job_title": job.title,
            "min_experience": job.min_experience,
            "required_skills": job.required_skills or [],
            "job_requirements": normalize_job_requirements(job.jd_requirements),
        },
    }


@celery_app.task(name="aggregate_job_results")
def aggregate_job_results(job_id: int, company_id: int):
    """Aggregate persisted applicants into jobs.results for UI and status polling."""
    with SessionLocal() as db:
        job = db.query(models.Job).filter(
            models.Job.id == job_id,
            models.Job.company_id == company_id,
        ).first()
        if not job:
            return

        applicants = (
            db.query(models.Applicant)
            .filter(
                models.Applicant.job_id == job_id,
                models.Applicant.company_id == company_id,
            )
            .order_by(models.Applicant.match_score.desc())
            .all()
        )

        results = _build_results_payload(job, applicants)
        job.results = results

        total = int(job.total_resumes or 0)
        processed = int(job.processed_resumes or 0)
        if total > 0 and processed >= total:
            job.status = "completed"
        elif job.status != "failed":
            job.status = "processing"

        db.commit()


def _persist_unreadable_applicant(db, job_id: int, company_id: int, pdf_bytes: bytes):
    """
    Persist a manual-review record for a resume whose text could not be
    extracted, instead of silently dropping it. A unique placeholder email
    is used so multiple unreadable files in one batch don't collide on the
    (job_id, email) unique constraint.
    """
    import uuid as _uuid
    placeholder_email = f"unreadable+{_uuid.uuid4().hex[:10]}@parse.error"
    applicant = models.Applicant(
        job_id=job_id,
        company_id=company_id,
        name="Unreadable Resume — Manual Review",
        email=placeholder_email,
        phone="",
        resume_text="",
        resume_pdf=pdf_bytes,
        years_experience=0,
        skills={},
        match_score=0,
        summary=json.dumps({
            "candidate_summary": "The resume PDF could not be parsed into readable text (likely a scan or image-only file).",
            "match_reasoning": "No facts could be extracted, so no score was computed.",
            "override_suggestion": "Open the original PDF and review it manually before rejecting.",
            "extraction_confidence": "low",
            "confidence_reason": "Resume text extraction failed.",
        }),
        status="knockout",
        extractable_text=False,
        knockout_flags=[{
            "type": "unreadable_resume",
            "severity": "hard",
            "reason": "Resume PDF could not be parsed — flagged for manual review",
        }],
    )
    db.add(applicant)
    db.commit()


@celery_app.task(
    name="process_resume",
    bind=True,
    autoretry_for=(),  # retries are explicit below — only for AI unavailability
    max_retries=4,
)
def process_resume(self, resume_b64: str, job_id: int, company_id: int):
    """Process a single resume PDF payload and persist applicant scoring results."""
    from services.ai_engine import AIServiceUnavailableError

    # Progress is advanced exactly once, in the finally block. This flag is
    # cleared only when the task is being retried (the resume has NOT been
    # processed yet). Note the previous version advanced progress inside the
    # body AND in finally on early returns, double-counting processed_resumes
    # and marking jobs complete before all resumes were actually handled.
    advance_progress = True

    with SessionLocal() as db:
        try:
            job = db.query(models.Job).filter(
                models.Job.id == job_id,
                models.Job.company_id == company_id,
            ).first()
            if not job:
                return

            pdf_bytes = base64.b64decode(resume_b64)

            resume_text = extract_text_from_pdf(pdf_bytes)
            if not resume_text or len(resume_text.strip()) < 50:
                # Persist for manual review instead of silently vanishing —
                # recruiters previously saw "50 uploaded, 47 processed" with
                # no trace of the missing three.
                _persist_unreadable_applicant(db, job_id, company_id, pdf_bytes)
                return

            email_match = re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", resume_text)
            pre_scan_email = email_match.group(0).lower() if email_match else None

            if pre_scan_email:
                duplicate = (
                    db.query(models.Applicant)
                    .filter(
                        models.Applicant.job_id == job_id,
                        models.Applicant.email == pre_scan_email,
                    )
                    .first()
                )
                if duplicate:
                    return  # progress advanced once in finally

            normalized_requirements = normalize_job_requirements(job.jd_requirements)
            try:
                candidate_data: Dict[str, Any] = asyncio.run(
                    extract_candidate_facts(resume_text, normalized_requirements)
                )
            except AIServiceUnavailableError as exc:
                # Transient Vertex AI failure — retry with exponential
                # backoff rather than mis-scoring the candidate as
                # unreadable. Progress is NOT advanced on retry.
                logger.warning(
                    "AI extraction unavailable for job_id=%s, retrying (%s/%s): %s",
                    job_id, self.request.retries + 1, self.max_retries, exc,
                )
                advance_progress = False
                raise self.retry(exc=exc, countdown=min(600, 30 * (2 ** self.request.retries)))

            # ── Build job config for scoring ──────────────────────────────
            effective_min_exp = (
                normalized_requirements.get("minimum_years_experience", 0)
                if normalized_requirements.get("minimum_years_experience", 0) > 0
                else float(job.min_experience or 0)
            )
            job_config: Dict[str, Any] = {
                "title": job.title or "",
                "min_experience": effective_min_exp,
                "required_skills": merge_required_skills(job.required_skills, normalized_requirements.get("must_have_skills")),
                "nice_to_have_skills": job.nice_to_have_skills or [],
                # "Not specified" (not "bachelor") — never invent an education
                # requirement the employer didn't set.
                "required_education": normalized_requirements.get("education_requirement") or "Not specified",
                "department": job.department,
                "require_cover_letter": job.require_cover_letter or False,
                "require_portfolio": job.require_portfolio or False,
                "require_linkedin": job.require_linkedin or False,
                "custom_questions": job.custom_questions or [],
                "work_location_type": job.work_location_type,
                "application_deadline": job.application_deadline,
                "offers_visa_sponsorship": normalized_requirements.get("offers_visa_sponsorship"),
            }

            candidate_skill_names = _extract_skill_names_for_embeddings(
                candidate_data.get("skills_detailed")
            )
            candidate_skill_embeddings = (
                get_skill_embeddings_sync(candidate_skill_names)
                if candidate_skill_names
                else {}
            )
            if candidate_skill_names and not candidate_skill_embeddings:
                logger.error(
                    "Candidate skill embedding generation failed for job_id=%s email=%s",
                    job_id,
                    candidate_data.get("email", pre_scan_email),
                )

            required_skill_names = [
                str(skill).strip()
                for skill in (job_config.get("required_skills") or [])
                if str(skill).strip()
            ]
            required_skill_embeddings = _get_or_create_required_skill_embeddings(
                db,
                job,
                required_skill_names,
            )

            # ── Score ─────────────────────────────────────────────────────
            total_score, score_breakdown = calculate_deterministic_score(
                candidate_data,
                job_config,
                required_skill_embeddings=required_skill_embeddings,
                candidate_skill_embeddings=candidate_skill_embeddings,
            )

            # ── Knockout ──────────────────────────────────────────────────
            knockout_flags = evaluate_knockout_filters(
                candidate_data, job_config,
            )
            has_hard_knockout = any(
                f["severity"] == "hard" for f in knockout_flags
            )
            if has_hard_knockout:
                total_score = 0

            # ── Signals & bucket ──────────────────────────────────────────
            candidate_signals = generate_candidate_signals(candidate_data, job_config)
            bucket = assign_bucket(total_score, has_hard_knockout)
            candidate_status = bucket_to_status(bucket, has_hard_knockout)

            # ── Summary ───────────────────────────────────────────────────
            try:
                candidate_summary = generate_candidate_summary_sync(
                    candidate_data, score_breakdown, bucket, knockout_flags, job_config,
                )
            except Exception as e:
                logger.error(f"Summary generation failed for {pre_scan_email}: {e}")
                candidate_summary = {
                    "candidate_summary": None,
                    "match_reasoning": f"Assigned to {bucket} with score {total_score}/100.",
                    "override_suggestion": None,
                    "extraction_confidence": "low",
                    "confidence_reason": "Summary generation encountered an error.",
                }

            # Store the PDF in object storage; keep the DB slim. Fall back to
            # the in-DB BLOB only if storage is unavailable.
            resume_key = storage.store_resume(
                company_id, pdf_bytes, candidate_data.get("name", "resume")
            )
            applicant = models.Applicant(
                job_id=job_id,
                company_id=company_id,
                name=candidate_data.get("name", "Unknown"),
                email=candidate_data.get("email", pre_scan_email or "N/A"),
                phone=candidate_data.get("phone", ""),
                resume_text=resume_text[:10000],
                resume_file_key=resume_key,
                resume_pdf=None if resume_key else pdf_bytes,
                years_experience=max(0, int(round(float(candidate_data.get("total_years_experience", 0) or 0.0)))),
                skills=candidate_data.get("skills_with_years", {}),
                skill_embeddings=candidate_skill_embeddings,
                match_score=total_score,
                summary=_serialize_summary(candidate_summary),
                status=candidate_status,
                breakdown=score_breakdown,
                # Enriched extraction fields
                skills_detailed=candidate_data.get("skills_detailed"),
                extracted_jobs=candidate_data.get("jobs"),
                extracted_education=candidate_data.get("education"),
                has_measurable_impact=candidate_data.get("has_measurable_impact"),
                has_contact_info=candidate_data.get("has_contact_info"),
                has_clear_job_titles=candidate_data.get("has_clear_job_titles"),
                employment_gaps=candidate_data.get("employment_gaps"),
                average_tenure_years=candidate_data.get("average_tenure_years"),
                extractable_text=candidate_data.get("extractable_text", True),
                cover_letter_analysis=candidate_data.get("cover_letter_analysis"),
                custom_answer_analysis=candidate_data.get("custom_answer_analysis"),
                # Score fields
                score_breakdown=score_breakdown,
                knockout_flags=knockout_flags,
                candidate_signals=candidate_signals,
            )
            db.add(applicant)
            db.commit()

        except IntegrityError as e:
            db.rollback()
            if "uq_applicants_job_email" not in str(e) and "UNIQUE constraint failed: applicants.job_id, applicants.email" not in str(e):
                raise
        finally:
            if advance_progress:
                try:
                    _advance_job_progress(db, job_id, company_id)
                    aggregate_job_results.delay(job_id, company_id)
                except Exception:
                    db.rollback()


@celery_app.task(name="process_public_resume", bind=True, max_retries=4)
def process_public_resume(
    self,
    resume_b64: str,
    job_id: int,
    company_id: int,
    submitted_name: str,
    submitted_email: str,
    submitted_phone: str | None = None,
    linkedin_url: str | None = None,
    portfolio_url: str | None = None,
    cover_letter: str | None = None,
    custom_answers: list[dict[str, str]] | None = None,
    **extra_payload,
):
    """Process a public portal submission and increment application_count on success."""
    if cover_letter is None:
        cover_letter = extra_payload.get("cover_letter")
    if custom_answers is None:
        custom_answers = extra_payload.get("custom_answers")
    with SessionLocal() as db:
        try:
            job = db.query(models.Job).filter(
                models.Job.id == job_id,
                models.Job.company_id == company_id,
            ).first()
            if not job:
                return

            pdf_bytes = base64.b64decode(resume_b64)

            resume_text = extract_text_from_pdf(pdf_bytes)
            if not resume_text or len(resume_text.strip()) < 50:
                resume_text = "Unable to extract text from resume."

            duplicate = (
                db.query(models.Applicant)
                .filter(
                    models.Applicant.job_id == job_id,
                    models.Applicant.email == submitted_email,
                )
                .first()
            )
            if duplicate:
                return

            normalized_requirements = normalize_job_requirements(job.jd_requirements)
            from services.ai_engine import AIServiceUnavailableError
            try:
                candidate_data: Dict[str, Any] = asyncio.run(
                    extract_candidate_facts(resume_text, normalized_requirements)
                )
            except AIServiceUnavailableError as exc:
                logger.warning(
                    "AI extraction unavailable for public submission job_id=%s email=%s, retrying (%s/%s): %s",
                    job_id, submitted_email, self.request.retries + 1, self.max_retries, exc,
                )
                raise self.retry(exc=exc, countdown=min(600, 30 * (2 ** self.request.retries)))

            if submitted_name and submitted_name.strip():
                candidate_data["name"] = submitted_name.strip()
            candidate_data["email"] = submitted_email.strip().lower()
            if submitted_phone and submitted_phone.strip():
                candidate_data["phone"] = submitted_phone.strip()

            # Merge form-submission fields so scoring/signals can see them
            candidate_data["cover_letter"] = cover_letter
            candidate_data["portfolio_url"] = portfolio_url
            candidate_data["linkedin_url"] = linkedin_url

            # ── Build job config for scoring ──────────────────────────────
            effective_min_exp = (
                normalized_requirements.get("minimum_years_experience", 0)
                if normalized_requirements.get("minimum_years_experience", 0) > 0
                else float(job.min_experience or 0)
            )
            job_config: Dict[str, Any] = {
                "title": job.title or "",
                "min_experience": effective_min_exp,
                "required_skills": merge_required_skills(job.required_skills, normalized_requirements.get("must_have_skills")),
                "nice_to_have_skills": job.nice_to_have_skills or [],
                "required_education": normalized_requirements.get("education_requirement") or "Not specified",
                "department": job.department,
                "require_cover_letter": job.require_cover_letter or False,
                "require_portfolio": job.require_portfolio or False,
                "require_linkedin": job.require_linkedin or False,
                "custom_questions": job.custom_questions or [],
                "work_location_type": job.work_location_type,
                "application_deadline": job.application_deadline,
                "offers_visa_sponsorship": normalized_requirements.get("offers_visa_sponsorship"),
            }

            candidate_skill_names = _extract_skill_names_for_embeddings(
                candidate_data.get("skills_detailed")
            )
            candidate_skill_embeddings = (
                get_skill_embeddings_sync(candidate_skill_names)
                if candidate_skill_names
                else {}
            )
            if candidate_skill_names and not candidate_skill_embeddings:
                logger.error(
                    "Candidate skill embedding generation failed for public submission job_id=%s email=%s",
                    job_id,
                    submitted_email,
                )

            required_skill_names = [
                str(skill).strip()
                for skill in (job_config.get("required_skills") or [])
                if str(skill).strip()
            ]
            required_skill_embeddings = _get_or_create_required_skill_embeddings(
                db,
                job,
                required_skill_names,
            )

            # ── Score ─────────────────────────────────────────────────────
            total_score, score_breakdown = calculate_deterministic_score(
                candidate_data,
                job_config,
                required_skill_embeddings=required_skill_embeddings,
                candidate_skill_embeddings=candidate_skill_embeddings,
            )

            # ── Knockout ──────────────────────────────────────────────────
            knockout_flags = evaluate_knockout_filters(
                candidate_data, job_config,
            )
            has_hard_knockout = any(
                f["severity"] == "hard" for f in knockout_flags
            )
            if has_hard_knockout:
                total_score = 0

            # ── Signals & bucket ──────────────────────────────────────────
            candidate_signals = generate_candidate_signals(candidate_data, job_config)
            bucket = assign_bucket(total_score, has_hard_knockout)
            candidate_status = bucket_to_status(bucket, has_hard_knockout)

            # ── Summary ───────────────────────────────────────────────────
            try:
                candidate_summary = generate_candidate_summary_sync(
                    candidate_data, score_breakdown, bucket, knockout_flags, job_config,
                )
            except Exception as e:
                logger.error(f"Summary generation failed for {submitted_email}: {e}")
                candidate_summary = {
                    "candidate_summary": None,
                    "match_reasoning": f"Assigned to {bucket} with score {total_score}/100.",
                    "override_suggestion": None,
                    "extraction_confidence": "low",
                    "confidence_reason": "Summary generation encountered an error.",
                }

            resume_key = storage.store_resume(
                company_id, pdf_bytes, candidate_data.get("name", "resume")
            )
            applicant = models.Applicant(
                job_id=job_id,
                company_id=company_id,
                name=candidate_data.get("name", submitted_name or "Unknown"),
                email=candidate_data.get("email", submitted_email),
                phone=candidate_data.get("phone", submitted_phone or ""),
                resume_text=resume_text[:10000],
                resume_file_key=resume_key,
                resume_pdf=None if resume_key else pdf_bytes,
                years_experience=max(0, int(round(float(candidate_data.get("total_years_experience", 0) or 0.0)))),
                skills=candidate_data.get("skills_with_years", {}),
                skill_embeddings=candidate_skill_embeddings,
                match_score=total_score,
                summary=_serialize_summary(candidate_summary),
                status=candidate_status,
                breakdown=score_breakdown,
                cover_letter=cover_letter,
                linkedin_url=linkedin_url,
                portfolio_url=portfolio_url,
                custom_answers=custom_answers,
                # Enriched extraction fields
                skills_detailed=candidate_data.get("skills_detailed"),
                extracted_jobs=candidate_data.get("jobs"),
                extracted_education=candidate_data.get("education"),
                has_measurable_impact=candidate_data.get("has_measurable_impact"),
                has_contact_info=candidate_data.get("has_contact_info"),
                has_clear_job_titles=candidate_data.get("has_clear_job_titles"),
                employment_gaps=candidate_data.get("employment_gaps"),
                average_tenure_years=candidate_data.get("average_tenure_years"),
                extractable_text=candidate_data.get("extractable_text", True),
                cover_letter_analysis=candidate_data.get("cover_letter_analysis"),
                custom_answer_analysis=candidate_data.get("custom_answer_analysis"),
                # Score fields
                score_breakdown=score_breakdown,
                knockout_flags=knockout_flags,
                candidate_signals=candidate_signals,
            )
            db.add(applicant)
            db.flush()

            db.query(models.Job).filter(
                models.Job.id == job_id,
                models.Job.company_id == company_id,
            ).update(
                {"application_count": func.coalesce(models.Job.application_count, 0) + 1}
            )

            db.commit()

            # Confirmation email to the candidate (safe no-op if email is
            # not configured — see services/email.py).
            try:
                company = db.query(models.Company).filter(
                    models.Company.id == company_id
                ).first()
                email_service.send_templated_email(
                    db, company_id, applicant.email, "application_received",
                    fields={
                        "candidate_name": applicant.name or "there",
                        "job_title": job.title if job else "the role",
                        "company_name": company.name if company else "our team",
                    },
                    applicant_id=applicant.id,
                )
            except Exception as _e:
                logger.warning("application_received email failed: %s", _e)

        except IntegrityError as e:
            db.rollback()
            if "uq_applicants_job_email" not in str(e) and "UNIQUE constraint failed: applicants.job_id, applicants.email" not in str(e):
                raise
        finally:
            try:
                aggregate_job_results.delay(job_id, company_id)
            except Exception:
                pass


@celery_app.task(name="extract_jd_requirements_task")
def extract_jd_requirements_task(job_id: int, company_id: int, job_description: str):
    """Extract and persist JD requirements asynchronously for newly created jobs."""
    requirements = asyncio.run(extract_jd_requirements(job_description or ""))
    if not requirements:
        return

    with SessionLocal() as db:
        job = db.query(models.Job).filter(
            models.Job.id == job_id,
            models.Job.company_id == company_id,
        ).first()
        if not job:
            return
        job.jd_requirements = requirements
        db.commit()
