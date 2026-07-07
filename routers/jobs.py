from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import func, case
from datetime import date
from database import get_db
import models, schemas
from services.auth import get_current_user
from services.tasks import extract_jd_requirements_task

router = APIRouter(prefix="/jobs", tags=["Jobs"])


def _validate_job_payload(payload: dict):
    salary_min = payload.get("salary_min")
    salary_max = payload.get("salary_max")
    if salary_min is not None and salary_max is not None and salary_max <= salary_min:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="salary_max must be greater than salary_min.",
        )

    today = date.today()
    application_deadline = payload.get("application_deadline")
    if application_deadline is not None and application_deadline <= today:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="application_deadline must be a future date.",
        )

    target_hire_date = payload.get("target_hire_date")
    if target_hire_date is not None and target_hire_date <= today:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="target_hire_date must be a future date.",
        )

    custom_questions = payload.get("custom_questions")
    if custom_questions is not None and len(custom_questions) > 5:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="custom_questions can contain at most 5 items.",
        )


@router.post("/", response_model=schemas.JobResponse, status_code=status.HTTP_201_CREATED)
async def create_job(
    job: schemas.JobCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """
    Create a new job posting with Level 1 JD normalization.
    The job is automatically associated with the authenticated user's company —
    tenants cannot create jobs on behalf of another company.
    
    The system will automatically extract structured requirements from the
    job description using AI (skills, minimum years, education, visa sponsorship).
    """
    job_data = job.model_dump()
    _validate_job_payload(job_data)

    new_job = models.Job(
        **job_data,
        company_id=current_user.company_id,  # ← injected from JWT, never from client
    )
    db.add(new_job)
    db.commit()
    db.refresh(new_job)

    if new_job.status == "Live":
        extract_jd_requirements_task.delay(new_job.id, current_user.company_id, new_job.description or "")
    
    return new_job


@router.get("/", response_model=list[schemas.JobResponse])
def get_jobs(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """
    List all jobs belonging to the authenticated user's company.
    Data from other tenants is never exposed.
    """
    return (
        db.query(models.Job)
        .filter(models.Job.company_id == current_user.company_id)
        .all()
    )


@router.get("/{job_id}", response_model=schemas.JobResponse)
def get_job(
    job_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Fetch a single job, enforcing tenant ownership."""
    job = (
        db.query(models.Job)
        .filter(
            models.Job.id == job_id,
            models.Job.company_id == current_user.company_id,
        )
        .first()
    )
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found.")
    return job


@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_job(
    job_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Delete a job. Only admins in the same company are allowed."""
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins can delete jobs.",
        )
    job = (
        db.query(models.Job)
        .filter(
            models.Job.id == job_id,
            models.Job.company_id == current_user.company_id,
        )
        .first()
    )
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found.")
    # Explicitly delete all applicants first (belt-and-suspenders for SQLite
    # which does not enforce FK constraints by default).
    db.query(models.Applicant).filter(models.Applicant.job_id == job_id).delete()
    db.delete(job)
    db.commit()


@router.put("/{job_id}", response_model=schemas.JobResponse)
def update_job(
    job_id: int,
    job_update: schemas.JobUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """
    Update a job's details.
    Only users in the same company can update their jobs.
    """
    job = (
        db.query(models.Job)
        .filter(
            models.Job.id == job_id,
            models.Job.company_id == current_user.company_id,
        )
        .first()
    )
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found.")

    update_data = job_update.model_dump(exclude_unset=True)
    _validate_job_payload(update_data)

    previous_status = job.status or "Draft"
    next_status = update_data.get("status", previous_status)

    jd_trigger_fields = {
        "title",
        "description",
        "required_skills",
        "required_experience",
        "required_education",
    }
    jd_content_changed = any(field in update_data for field in jd_trigger_fields)

    should_extract_jd = (
        (previous_status == "Draft" and next_status == "Live")
        or (previous_status == "Live" and next_status == "Live" and jd_content_changed)
    )

    # Update only provided fields
    for field, value in update_data.items():
        setattr(job, field, value)

    db.commit()
    db.refresh(job)

    if should_extract_jd:
        extract_jd_requirements_task.delay(job.id, current_user.company_id, job.description or "")

    return job


@router.put("/{job_id}/form-config", response_model=schemas.JobResponse)
def update_job_form_config(
    job_id: int,
    form_config: schemas.JobFormConfig,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """
    Update the public application form configuration for a job.
    
    Example form_config:
    {
        "require_linkedin": true,
        "require_portfolio": false,
        "custom_questions": ["Why do you want to work here?", "What's your biggest achievement?"]
    }
    """
    job = (
        db.query(models.Job)
        .filter(
            models.Job.id == job_id,
            models.Job.company_id == current_user.company_id,
        )
        .first()
    )
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found.")

    job.form_config = form_config.model_dump(exclude_none=True)
    db.commit()
    db.refresh(job)
    return job


@router.patch("/{job_id}/status", response_model=schemas.JobResponse)
def toggle_job_status(
    job_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Toggle job active/inactive status. Only admins can modify status."""
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins can change job status."
        )
    
    job = (
        db.query(models.Job)
        .filter(
            models.Job.id == job_id,
            models.Job.company_id == current_user.company_id,
        )
        .first()
    )
    
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found."
        )
    
    # Toggle the status
    job.is_active = not job.is_active
    db.commit()
    db.refresh(job)
    return job


@router.get("/dashboard/stats", response_model=schemas.DashboardStatsResponse)
def get_dashboard_statistics(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Get overall dashboard statistics for the authenticated user's company."""
    # Job statistics
    jobs = db.query(models.Job).filter(
        models.Job.company_id == current_user.company_id
    ).all()
    
    total_jobs = len(jobs)
    active_jobs = sum(1 for j in jobs if j.is_active)
    
    # Applicant statistics
    applicants = db.query(models.Applicant).filter(
        models.Applicant.company_id == current_user.company_id
    ).all()
    
    total_applicants = len(applicants)
    shortlisted_applicants = sum(1 for a in applicants if a.status == "shortlisted")
    pending_review = sum(1 for a in applicants if a.status == "review")
    
    # Recent applicants (top 10 by score)
    recent_applicants = (
        db.query(models.Applicant)
        .filter(models.Applicant.company_id == current_user.company_id)
        .order_by(models.Applicant.match_score.desc())
        .limit(10)
        .all()
    )
    
    return schemas.DashboardStatsResponse(
        total_jobs=total_jobs,
        active_jobs=active_jobs,
        total_applicants=total_applicants,
        shortlisted_applicants=shortlisted_applicants,
        pending_review=pending_review,
        recent_applicants=recent_applicants
    )


@router.get("/{job_id}/stats", response_model=schemas.JobStatsResponse)
def get_job_statistics(
    job_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Get statistics for a specific job with tenant isolation."""
    # Verify job exists and belongs to user's company
    job = (
        db.query(models.Job)
        .filter(
            models.Job.id == job_id,
            models.Job.company_id == current_user.company_id,
        )
        .first()
    )
    
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found."
        )
    
    # Calculate statistics
    applicants = db.query(models.Applicant).filter(
        models.Applicant.job_id == job_id
    ).all()
    
    total_applicants = len(applicants)
    shortlisted = sum(1 for a in applicants if a.status == "shortlisted")
    under_review = sum(1 for a in applicants if a.status == "review")
    rejected = sum(1 for a in applicants if a.status == "rejected")
    interviewed = sum(1 for a in applicants if a.status == "interviewed")
    hired = sum(1 for a in applicants if a.status == "hired")
    
    average_score = (
        sum(a.match_score for a in applicants) / total_applicants
        if total_applicants > 0
        else 0.0
    )
    
    return schemas.JobStatsResponse(
        job_id=job_id,
        job_title=job.title,
        total_applicants=total_applicants,
        shortlisted=shortlisted,
        under_review=under_review,
        rejected=rejected,
        interviewed=interviewed,
        hired=hired,
        average_score=round(average_score, 1)
    )