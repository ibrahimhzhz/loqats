from fastapi import APIRouter, UploadFile, File, Depends, Form, HTTPException, status, Response
from sqlalchemy.orm import Session
from database import get_db
import models, schemas
from services.pdf_parser import extract_text_from_pdf
from services.ai_engine import (
    extract_candidate_facts,
    normalize_job_requirements,
    merge_required_skills,
    generate_candidate_summary_sync,
    get_skill_embeddings_sync,
)
from services.auth import get_current_user
from services.tasks import aggregate_job_results
from scoring import (
    calculate_deterministic_score as calculate_pipeline_score,
    evaluate_knockout_filters,
    generate_candidate_signals,
    assign_bucket,
    bucket_to_status,
)
from typing import Optional, Any
import json
import re
from urllib.parse import quote

router = APIRouter(prefix="/applicants", tags=["Applicants"])


def _extract_skill_names_for_embeddings(skills_detailed: list[dict[str, Any]] | None) -> list[str]:
    """Return de-duplicated skill names from extracted skills_detailed payload."""
    names: list[str] = []
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


def _serialize_summary(summary_payload: Any) -> str:
    """Store structured AI summary payloads as JSON text."""
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

@router.post("/apply/{job_id}")
async def apply_for_job(
    job_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """
    Single-resume apply endpoint.
    Mirrors the bulk pipeline: dedup → extract → knockout → deterministic score.
    """
    # Stage 0: Job lookup
    job = db.query(models.Job).filter(models.Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if not job.is_active:
        raise HTTPException(status_code=400, detail="This job posting is no longer active")

    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF resumes are accepted")

    # Stage 0b: PDF parse
    file_content = await file.read()
    if len(file_content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Resume file too large (max 10MB)")

    text = extract_text_from_pdf(file_content)
    if len(text) < 50:
        raise HTTPException(status_code=400, detail="Could not extract text from PDF")

    # ── Stage 1: Fast deduplication ───────────────────────────────────────────
    email_match = re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", text)
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
            raise HTTPException(
                status_code=409,
                detail="A resume with this email has already been submitted for this job.",
            )

    # ── Stage 2: Deep fact extraction (single AI call) ─────────────────────────
    # Uses the SAME pipeline as bulk-screen and public applications.
    # (Previously this endpoint used a legacy 40/40/20 scoring engine with
    # different knockout rules — the same resume could receive a different
    # score and status depending on which upload path it entered through.)
    normalized_requirements = normalize_job_requirements(job.jd_requirements)
    candidate_data = await extract_candidate_facts(text, normalized_requirements)

    total_years_experience: float = candidate_data["total_years_experience"]

    effective_min_exp = (
        normalized_requirements.get("minimum_years_experience", 0)
        if normalized_requirements.get("minimum_years_experience", 0) > 0
        else float(job.min_experience or 0)
    )
    job_config: dict[str, Any] = {
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

    # ── Stage 3: Embeddings (semantic skill matching) ───────────────────────
    candidate_skill_names = _extract_skill_names_for_embeddings(
        candidate_data.get("skills_detailed")
    )
    candidate_skill_embeddings = (
        get_skill_embeddings_sync(candidate_skill_names) if candidate_skill_names else {}
    )
    if isinstance(job.required_skill_embeddings, dict):
        required_skill_embeddings = job.required_skill_embeddings
    else:
        required_skill_names = [
            str(s).strip() for s in (job_config.get("required_skills") or []) if str(s).strip()
        ]
        required_skill_embeddings = (
            get_skill_embeddings_sync(required_skill_names) if required_skill_names else {}
        ) or {}
        job.required_skill_embeddings = required_skill_embeddings

    # ── Stage 4: Unified deterministic scoring + knockouts ──────────────────
    total_score, score_breakdown = calculate_pipeline_score(
        candidate_data,
        job_config,
        required_skill_embeddings=required_skill_embeddings,
        candidate_skill_embeddings=candidate_skill_embeddings,
    )

    knockout_flags = evaluate_knockout_filters(candidate_data, job_config)
    has_hard_knockout = any(f.get("severity") == "hard" for f in knockout_flags)
    if has_hard_knockout:
        total_score = 0

    candidate_signals = generate_candidate_signals(candidate_data, job_config)
    bucket = assign_bucket(total_score, has_hard_knockout)
    assessment_status = bucket_to_status(bucket, has_hard_knockout)

    try:
        candidate_summary = generate_candidate_summary_sync(
            candidate_data, score_breakdown, bucket, knockout_flags, job_config,
        )
    except Exception:
        candidate_summary = {
            "candidate_summary": None,
            "match_reasoning": f"Assigned to {bucket} with score {total_score}/100.",
            "override_suggestion": None,
            "extraction_confidence": "low",
            "confidence_reason": "Summary generation encountered an error.",
        }

    # ── Stage 5: Persist (same enriched fields as pipeline records) ─────────
    new_applicant = models.Applicant(
        job_id=job_id,
        company_id=job.company_id,
        name=candidate_data.get("name", "Unknown"),
        email=candidate_data.get("email", pre_scan_email or "N/A"),
        phone=candidate_data.get("phone", ""),
        years_experience=max(0, int(round(total_years_experience))),
        skills=candidate_data.get("skills_with_years", {}),
        skill_embeddings=candidate_skill_embeddings,
        resume_text=text[:10000],
        resume_pdf=file_content,  # Store original PDF for download
        match_score=total_score,
        summary=_serialize_summary(candidate_summary),
        status=assessment_status,
        breakdown=score_breakdown,
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
        score_breakdown=score_breakdown,
        knockout_flags=knockout_flags,
        candidate_signals=candidate_signals,
    )
    db.add(new_applicant)
    db.commit()
    db.refresh(new_applicant)

    return {"message": "Application received", "score": total_score, "status": assessment_status}


@router.post("/{applicant_id}/reprocess", response_model=schemas.ApplicantResponse)
async def reprocess_applicant_resume(
    applicant_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Re-run extraction + scoring for a single applicant using stored resume data."""
    applicant = db.query(models.Applicant).filter(
        models.Applicant.id == applicant_id,
        models.Applicant.company_id == current_user.company_id,
    ).first()

    if not applicant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Applicant not found or not authorized",
        )

    job = db.query(models.Job).filter(
        models.Job.id == applicant.job_id,
        models.Job.company_id == current_user.company_id,
    ).first()
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found or not authorized",
        )

    # Prefer reparsing original PDF so updated extraction logic takes effect.
    resume_text = (applicant.resume_text or "").strip()
    if applicant.resume_pdf:
        reparsed_text = extract_text_from_pdf(applicant.resume_pdf)
        if reparsed_text and len(reparsed_text.strip()) >= 50:
            resume_text = reparsed_text.strip()

    if len(resume_text) < 50:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Resume text is unavailable for reprocessing.",
        )

    normalized_requirements = normalize_job_requirements(job.jd_requirements)
    candidate_data = await extract_candidate_facts(
        resume_text,
        normalized_requirements,
        cover_letter_text=applicant.cover_letter,
        custom_questions_and_answers=applicant.custom_answers,
    )

    # Preserve recruiter-visible contact fields from the existing record.
    candidate_data["name"] = (applicant.name or candidate_data.get("name") or "Unknown").strip()
    candidate_data["email"] = (applicant.email or candidate_data.get("email") or "N/A").strip().lower()
    candidate_data["phone"] = (applicant.phone or candidate_data.get("phone") or "").strip()
    candidate_data["cover_letter"] = applicant.cover_letter
    candidate_data["portfolio_url"] = applicant.portfolio_url
    candidate_data["linkedin_url"] = applicant.linkedin_url

    effective_min_exp = (
        normalized_requirements.get("minimum_years_experience", 0)
        if normalized_requirements.get("minimum_years_experience", 0) > 0
        else float(job.min_experience or 0)
    )
    job_config: dict[str, Any] = {
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

    candidate_skill_names = _extract_skill_names_for_embeddings(candidate_data.get("skills_detailed"))
    candidate_skill_embeddings = (
        get_skill_embeddings_sync(candidate_skill_names)
        if candidate_skill_names
        else {}
    )

    required_skill_names = [
        str(skill).strip()
        for skill in (job_config.get("required_skills") or [])
        if str(skill).strip()
    ]
    if isinstance(job.required_skill_embeddings, dict):
        required_skill_embeddings = job.required_skill_embeddings
    elif required_skill_names:
        required_skill_embeddings = get_skill_embeddings_sync(required_skill_names) or {}
        job.required_skill_embeddings = required_skill_embeddings
    else:
        required_skill_embeddings = {}

    total_score, score_breakdown = calculate_pipeline_score(
        candidate_data,
        job_config,
        required_skill_embeddings=required_skill_embeddings,
        candidate_skill_embeddings=candidate_skill_embeddings,
    )

    knockout_flags = evaluate_knockout_filters(candidate_data, job_config)
    has_hard_knockout = any(flag.get("severity") == "hard" for flag in knockout_flags)
    if has_hard_knockout:
        total_score = 0

    candidate_signals = generate_candidate_signals(candidate_data, job_config)
    bucket = assign_bucket(total_score, has_hard_knockout)
    candidate_status = bucket_to_status(bucket, has_hard_knockout)

    try:
        candidate_summary = generate_candidate_summary_sync(
            candidate_data,
            score_breakdown,
            bucket,
            knockout_flags,
            job_config,
        )
    except Exception:
        candidate_summary = {
            "candidate_summary": None,
            "match_reasoning": f"Assigned to {bucket} with score {total_score}/100.",
            "override_suggestion": None,
            "extraction_confidence": "low",
            "confidence_reason": "Summary generation encountered an error.",
        }

    applicant.name = candidate_data.get("name", applicant.name or "Unknown")
    applicant.email = candidate_data.get("email", applicant.email or "N/A")
    applicant.phone = candidate_data.get("phone", applicant.phone or "")
    applicant.resume_text = resume_text[:10000]
    applicant.years_experience = max(
        0,
        int(round(float(candidate_data.get("total_years_experience", 0) or 0.0))),
    )
    applicant.skills = candidate_data.get("skills_with_years", {})
    applicant.skill_embeddings = candidate_skill_embeddings
    applicant.match_score = total_score
    applicant.summary = _serialize_summary(candidate_summary)
    applicant.status = candidate_status
    applicant.breakdown = score_breakdown
    applicant.skills_detailed = candidate_data.get("skills_detailed")
    applicant.extracted_jobs = candidate_data.get("jobs")
    applicant.extracted_education = candidate_data.get("education")
    applicant.has_measurable_impact = candidate_data.get("has_measurable_impact")
    applicant.has_contact_info = candidate_data.get("has_contact_info")
    applicant.has_clear_job_titles = candidate_data.get("has_clear_job_titles")
    applicant.employment_gaps = candidate_data.get("employment_gaps")
    applicant.average_tenure_years = candidate_data.get("average_tenure_years")
    applicant.extractable_text = candidate_data.get("extractable_text", True)
    applicant.cover_letter_analysis = candidate_data.get("cover_letter_analysis")
    applicant.custom_answer_analysis = candidate_data.get("custom_answer_analysis")
    applicant.score_breakdown = score_breakdown
    applicant.knockout_flags = knockout_flags
    applicant.candidate_signals = candidate_signals

    db.commit()
    db.refresh(applicant)

    try:
        aggregate_job_results.delay(job.id, current_user.company_id)
    except Exception:
        pass

    return applicant


@router.get("/", response_model=list[schemas.ApplicantResponse])
def get_applicants(
    job_id: Optional[int] = None,
    status_filter: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """
    List all applicants for the authenticated user's company.
    Enforces strict tenant isolation - users only see their company's applicants.
    
    Optional filters:
    - job_id: Filter by specific job
    - status_filter: Filter by status (new, shortlisted, review, rejected, etc.)
    """
    # Base query with tenant isolation
    query = db.query(models.Applicant).filter(
        models.Applicant.company_id == current_user.company_id
    )
    
    # Apply optional filters
    if job_id:
        # Verify the job belongs to the user's company
        job = db.query(models.Job).filter(
            models.Job.id == job_id,
            models.Job.company_id == current_user.company_id
        ).first()
        if not job:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to view applicants for this job"
            )
        query = query.filter(models.Applicant.job_id == job_id)
    
    if status_filter:
        query = query.filter(models.Applicant.status == status_filter)
    
    # Order by match score descending (best candidates first)
    applicants = query.order_by(models.Applicant.match_score.desc()).all()
    
    return applicants


@router.get("/{applicant_id}", response_model=schemas.ApplicantDetailResponse)
def get_applicant(
    applicant_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """
    Get detailed information about a specific applicant.
    Enforces tenant isolation - returns 403 if applicant belongs to another company.
    """
    applicant = db.query(models.Applicant).filter(
        models.Applicant.id == applicant_id,
        models.Applicant.company_id == current_user.company_id
    ).first()
    
    if not applicant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Applicant not found or not authorized"
        )
    
    return applicant


@router.put("/{applicant_id}/status", response_model=schemas.ApplicantResponse)
def update_applicant_status(
    applicant_id: int,
    status_update: schemas.ApplicantStatusUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """
    Update an applicant's status in the hiring workflow.
    Valid statuses: new, rejected, shortlisted, review, interviewed, hired
    Enforces tenant isolation.
    """
    # Verify applicant exists and belongs to user's company
    applicant = db.query(models.Applicant).filter(
        models.Applicant.id == applicant_id,
        models.Applicant.company_id == current_user.company_id
    ).first()
    
    if not applicant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Applicant not found or not authorized"
        )
    
    # Validate status value
    valid_statuses = ["new", "rejected", "shortlisted", "review", "interviewed", "hired"]
    if status_update.status not in valid_statuses:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid status. Must be one of: {', '.join(valid_statuses)}"
        )
    
    # Update status
    applicant.status = status_update.status
    db.commit()
    db.refresh(applicant)
    
    return applicant


@router.delete("/{applicant_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_applicant(
    applicant_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """
    Delete an applicant. Only admins in the same company are allowed.
    Enforces tenant isolation.
    """
    # Only admins can delete applicants
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins can delete applicants"
        )
    
    # Verify applicant exists and belongs to user's company
    applicant = db.query(models.Applicant).filter(
        models.Applicant.id == applicant_id,
        models.Applicant.company_id == current_user.company_id
    ).first()
    
    if not applicant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Applicant not found or not authorized"
        )
    
    db.delete(applicant)
    db.commit()


@router.put("/bulk/status", response_model=dict)
def bulk_update_applicant_status(
    bulk_update: schemas.BulkApplicantStatusUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """
    Bulk update the status of multiple applicants.
    Enforces tenant isolation - only updates applicants belonging to user's company.
    Returns count of updated applicants.
    """
    # Validate status value
    valid_statuses = ["new", "rejected", "shortlisted", "review", "interviewed", "hired"]
    if bulk_update.status not in valid_statuses:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid status. Must be one of: {', '.join(valid_statuses)}"
        )
    
    # Fetch applicants that belong to user's company
    applicants = db.query(models.Applicant).filter(
        models.Applicant.id.in_(bulk_update.applicant_ids),
        models.Applicant.company_id == current_user.company_id
    ).all()
    
    if not applicants:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No applicants found or not authorized"
        )
    
    # Update all fetched applicants
    updated_count = 0
    for applicant in applicants:
        applicant.status = bulk_update.status
        updated_count += 1
    
    db.commit()
    
    return {
        "message": f"Successfully updated {updated_count} applicants",
        "updated_count": updated_count,
        "requested_count": len(bulk_update.applicant_ids),
        "new_status": bulk_update.status
    }


@router.get("/{applicant_id}/download-resume")
def download_resume(
    applicant_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """
    Download the original resume PDF for an applicant.
    Enforces tenant isolation - only users from the same company can download.
    """
    # Verify applicant exists and belongs to user's company
    applicant = db.query(models.Applicant).filter(
        models.Applicant.id == applicant_id,
        models.Applicant.company_id == current_user.company_id
    ).first()
    
    if not applicant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Applicant not found or not authorized"
        )
    
    # Prefer object storage (resume_file_key); fall back to the legacy in-DB
    # BLOB for records created before storage was introduced.
    pdf_bytes = None
    if getattr(applicant, "resume_file_key", None):
        from services import storage
        pdf_bytes = storage.load_bytes(applicant.resume_file_key)
    if pdf_bytes is None:
        pdf_bytes = applicant.resume_pdf

    if not pdf_bytes:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Resume PDF not available for this applicant"
        )
    
    # Generate filename
    safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", (applicant.name or "resume")).strip("._") or "resume"
    filename = quote(f"{safe_name}_resume.pdf")
    
    # Return PDF with proper headers
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{filename}"
        }
    )