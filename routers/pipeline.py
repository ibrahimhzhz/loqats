"""
routers/pipeline.py — Hiring Pipeline Stage Management
=======================================================
PATCH /api/jobs/{job_id}/applicants/{applicant_id}/stage  → Move candidate to new stage
GET   /api/jobs/{job_id}/applicants/{applicant_id}/history → Full stage-change audit trail
GET   /api/jobs/{job_id}/pipeline                          → Kanban view grouped by stage
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from collections import defaultdict

from database import get_db
import models
import schemas
from services.auth import get_current_user

router = APIRouter(prefix="/api/jobs", tags=["Pipeline"])


# ==============================================================================
# HELPERS
# ==============================================================================

def _get_job_for_company(db: Session, job_id: int, company_id: int) -> models.Job:
    """Fetch a job scoped to a company, raising 404 on miss."""
    job = (
        db.query(models.Job)
        .filter(
            models.Job.id == job_id,
            models.Job.company_id == company_id,
        )
        .first()
    )
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found.",
        )
    return job


def _get_applicant_for_job(
    db: Session, applicant_id: int, job_id: int, company_id: int
) -> models.Applicant:
    """Fetch an applicant scoped to a job and company, raising 404 on miss."""
    applicant = (
        db.query(models.Applicant)
        .filter(
            models.Applicant.id == applicant_id,
            models.Applicant.job_id == job_id,
            models.Applicant.company_id == company_id,
        )
        .first()
    )
    if not applicant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Applicant not found or not authorized.",
        )
    return applicant


# ==============================================================================
# PATCH — Move candidate to a new pipeline stage
# ==============================================================================

@router.patch(
    "/{job_id}/applicants/{applicant_id}/stage",
    response_model=schemas.ApplicantPipelineResponse,
)
def update_applicant_stage(
    job_id: int,
    applicant_id: int,
    payload: schemas.PipelineStageUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """
    Move an applicant to a new hiring-pipeline stage.

    Business rules enforced:
      - Stage must be a valid pipeline stage.
      - Cannot move out of terminal stages (Hired, Rejected).
      - Cannot move backwards in the pipeline.
      - Stage change + audit log are written atomically.
    """
    # Tenant isolation — verify ownership chain
    _get_job_for_company(db, job_id, current_user.company_id)
    applicant = _get_applicant_for_job(db, applicant_id, job_id, current_user.company_id)

    new_stage = payload.stage

    # --- Validate stage value ---
    if new_stage not in models.PIPELINE_STAGES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid stage. Must be one of: {', '.join(models.PIPELINE_STAGES)}",
        )

    current_stage = applicant.pipeline_stage or "Applied"

    # --- Block moves out of terminal stages ---
    if current_stage in models.TERMINAL_STAGES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot change stage: applicant is already in terminal stage '{current_stage}'.",
        )

    # --- Block backward moves ---
    current_index = models.PIPELINE_STAGES.index(current_stage) if current_stage in models.PIPELINE_STAGES else 0
    new_index = models.PIPELINE_STAGES.index(new_stage)

    # "Rejected" is always allowed from any non-terminal stage (it's a special lateral move)
    if new_stage != "Rejected" and new_index < current_index:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Cannot move backwards from '{current_stage}' to '{new_stage}'. "
                f"Pipeline stages only move forward."
            ),
        )

    # --- No-op guard ---
    if new_stage == current_stage:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Applicant is already in stage '{current_stage}'.",
        )

    # --- Atomic write: update applicant + insert audit log ---
    try:
        applicant.pipeline_stage = new_stage
        # stage_updated_at is handled by onupdate=func.now() on the column,
        # but SQLite doesn't support server-side onupdate. Set explicitly.
        from sqlalchemy.sql import func as sqlfunc
        applicant.stage_updated_at = sqlfunc.now()

        log_entry = models.ApplicantStageLog(
            applicant_id=applicant.id,
            from_stage=current_stage,
            to_stage=new_stage,
            changed_by_recruiter_id=current_user.id,
            note=payload.note,
        )
        db.add(log_entry)
        db.commit()
        db.refresh(applicant)
    except Exception:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update pipeline stage. The change has been rolled back.",
        )

    # General audit trail (separate from the pipeline-specific stage log).
    try:
        from services import audit as _audit
        _audit.record(
            db, current_user.company_id, "applicant.stage_changed", actor=current_user,
            entity_type="applicant", entity_id=applicant.id,
            summary=f"{current_stage} → {new_stage}",
            meta={"from": current_stage, "to": new_stage, "note": payload.note},
        )
    except Exception:
        pass

    # Notify the candidate of the stage change, unless explicitly suppressed.
    # Rejections use the dedicated (softer) rejection template.
    if getattr(payload, "notify_candidate", True):
        try:
            from services import email as _email
            company = db.query(models.Company).filter(
                models.Company.id == current_user.company_id
            ).first()
            job = db.query(models.Job).filter(models.Job.id == job_id).first()
            template_key = "rejected" if new_stage == "Rejected" else "stage_changed"
            _email.send_templated_email(
                db, current_user.company_id, applicant.email, template_key,
                fields={
                    "candidate_name": applicant.name or "there",
                    "job_title": job.title if job else "the role",
                    "company_name": company.name if company else "our team",
                    "new_stage": new_stage,
                },
                applicant_id=applicant.id,
            )
        except Exception:
            pass

    return applicant


# ==============================================================================
# GET — Full stage-change audit history for an applicant
# ==============================================================================

@router.get(
    "/{job_id}/applicants/{applicant_id}/history",
    response_model=list[schemas.StageLogResponse],
)
def get_applicant_stage_history(
    job_id: int,
    applicant_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """
    Return the full stage-change audit trail for an applicant in
    chronological order, including who made each change and any notes.
    """
    _get_job_for_company(db, job_id, current_user.company_id)
    _get_applicant_for_job(db, applicant_id, job_id, current_user.company_id)

    logs = (
        db.query(models.ApplicantStageLog)
        .filter(models.ApplicantStageLog.applicant_id == applicant_id)
        .order_by(models.ApplicantStageLog.changed_at.asc())
        .all()
    )

    # Enrich with recruiter email for readability
    result = []
    for log in logs:
        recruiter = db.query(models.User).filter(models.User.id == log.changed_by_recruiter_id).first()
        result.append(
            schemas.StageLogResponse(
                id=log.id,
                from_stage=log.from_stage,
                to_stage=log.to_stage,
                changed_by_recruiter_id=log.changed_by_recruiter_id,
                recruiter_email=recruiter.email if recruiter else None,
                note=log.note,
                changed_at=log.changed_at,
            )
        )

    return result


# ==============================================================================
# GET — Kanban pipeline view grouped by stage
# ==============================================================================

@router.get("/{job_id}/pipeline")
def get_job_pipeline(
    job_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """
    Return all applicants for a job grouped by their current pipeline stage.

    Response shape (consumed by a Kanban board)::

        {
            "Applied": [...],
            "Recruiter Screen": [...],
            "Interview": [...],
            ...
        }
    """
    _get_job_for_company(db, job_id, current_user.company_id)

    applicants = (
        db.query(models.Applicant)
        .filter(
            models.Applicant.job_id == job_id,
            models.Applicant.company_id == current_user.company_id,
        )
        .order_by(models.Applicant.match_score.desc())
        .all()
    )

    # Pre-fill every stage key so the frontend always gets a complete shape
    grouped: dict[str, list] = {stage: [] for stage in models.PIPELINE_STAGES}

    for applicant in applicants:
        stage = applicant.pipeline_stage or "Applied"
        entry = schemas.PipelineApplicantResponse(
            id=applicant.id,
            name=applicant.name or "Unknown",
            email=applicant.email or "",
            match_score=applicant.match_score,
            status=applicant.status,
            pipeline_stage=stage,
            stage_updated_at=applicant.stage_updated_at,
        )
        if stage in grouped:
            grouped[stage].append(entry.model_dump())
        else:
            # Fallback for any unexpected stage value
            grouped.setdefault(stage, []).append(entry.model_dump())

    return grouped
