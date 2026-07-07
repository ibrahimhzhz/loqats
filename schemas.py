from pydantic import BaseModel, EmailStr, Field
from typing import List, Optional, Dict, Union
from datetime import datetime, date


# ==============================================================================
# COMPANY SCHEMAS
# ==============================================================================

class CompanyCreate(BaseModel):
    name: str
    subscription_tier: str = "free"

class CompanyResponse(BaseModel):
    id: int
    name: str
    subscription_tier: str
    created_at: datetime

    class Config:
        from_attributes = True


# ==============================================================================
# USER / AUTH SCHEMAS
# ==============================================================================

class UserCreate(BaseModel):
    """Payload to register a new user and (optionally) create their company."""
    email: EmailStr
    password: str
    company_name: str  # Used to create or look up the tenant on registration

class UserResponse(BaseModel):
    id: int
    email: EmailStr
    role: str
    company_id: int
    created_at: datetime

    class Config:
        from_attributes = True

class Token(BaseModel):
    """JWT response returned on successful login."""
    access_token: str
    token_type: str = "bearer"

class TokenData(BaseModel):
    """Claims extracted from a decoded JWT — used internally."""
    user_id: Optional[int] = None
    company_id: Optional[int] = None


# ==============================================================================
# JOB SCHEMAS
# ==============================================================================

class JobCreate(BaseModel):
    title: str
    description: str
    min_experience: int
    required_skills: List[str]
    department: Optional[str] = None
    job_type: Optional[str] = None
    work_location_type: Optional[str] = None
    office_location: Optional[str] = None
    openings: Optional[int] = 1
    status: Optional[str] = "Draft"
    salary_min: Optional[int] = None
    salary_max: Optional[int] = None
    currency: Optional[str] = "USD"
    pay_frequency: Optional[str] = "Annual"
    show_salary: Optional[bool] = True
    equity_bonus: Optional[str] = None
    nice_to_have_skills: Optional[List[str]] = None
    benefits: Optional[List[str]] = None
    require_cover_letter: Optional[bool] = False
    require_portfolio: Optional[bool] = False
    require_linkedin: Optional[bool] = False
    custom_questions: Optional[List[str]] = Field(default=None, max_length=5)
    hiring_manager: Optional[str] = None
    target_hire_date: Optional[date] = None
    application_deadline: Optional[date] = None
    visibility: Optional[str] = "Public"
    # NOTE: company_id is intentionally NOT here; it is injected server-side
    #       from the authenticated user's token so tenants can never spoof it.

class JobUpdate(BaseModel):
    """Schema for updating job details."""
    title: Optional[str] = None
    description: Optional[str] = None
    min_experience: Optional[int] = None
    required_skills: Optional[List[str]] = None
    is_active: Optional[bool] = None
    department: Optional[str] = None
    job_type: Optional[str] = None
    work_location_type: Optional[str] = None
    office_location: Optional[str] = None
    openings: Optional[int] = 1
    status: Optional[str] = "Draft"
    salary_min: Optional[int] = None
    salary_max: Optional[int] = None
    currency: Optional[str] = "USD"
    pay_frequency: Optional[str] = "Annual"
    show_salary: Optional[bool] = True
    equity_bonus: Optional[str] = None
    nice_to_have_skills: Optional[List[str]] = None
    benefits: Optional[List[str]] = None
    require_cover_letter: Optional[bool] = False
    require_portfolio: Optional[bool] = False
    require_linkedin: Optional[bool] = False
    custom_questions: Optional[List[str]] = Field(default=None, max_length=5)
    hiring_manager: Optional[str] = None
    target_hire_date: Optional[date] = None
    application_deadline: Optional[date] = None
    visibility: Optional[str] = "Public"

class JobResponse(JobCreate):
    id: int
    company_id: int
    is_active: bool
    status: str
    openings: int
    currency: str
    pay_frequency: str
    show_salary: bool
    require_cover_letter: bool
    require_portfolio: bool
    require_linkedin: bool
    visibility: str
    views: Optional[int] = 0
    application_count: Optional[int] = 0
    form_config: Optional[dict] = None
    required_skill_embeddings: Optional[dict] = None

    class Config:
        from_attributes = True

class JobStatsResponse(BaseModel):
    """Statistics for a specific job."""
    job_id: int
    job_title: str
    total_applicants: int
    shortlisted: int
    under_review: int
    rejected: int
    interviewed: int
    hired: int
    average_score: float

class DashboardStatsResponse(BaseModel):
    """Overall dashboard statistics for a company."""
    total_jobs: int
    active_jobs: int
    total_applicants: int
    shortlisted_applicants: int
    pending_review: int
    recent_applicants: List["ApplicantResponse"]


class JobFormConfig(BaseModel):
    require_linkedin: Optional[Union[bool, str]] = None
    require_portfolio: Optional[Union[bool, str]] = None
    custom_questions: Optional[List[str]] = None


# ==============================================================================
# APPLICANT SCHEMAS
# ==============================================================================

class ApplicantResponse(BaseModel):
    id: int
    job_id: int
    company_id: int
    name: str
    email: str
    match_score: int
    years_experience: int
    summary: str
    status: str
    cover_letter: Optional[str] = None
    breakdown: Optional[dict] = None
    pipeline_stage: Optional[str] = None
    stage_updated_at: Optional[datetime] = None

    # Enriched extraction signals (pipeline board & signal badges)
    extractable_text: Optional[bool] = None
    employment_gaps: Optional[bool] = None
    average_tenure_years: Optional[float] = None
    has_measurable_impact: Optional[bool] = None
    skill_embeddings: Optional[dict] = None
    score_breakdown: Optional[dict] = None
    knockout_flags: Optional[list] = None
    candidate_signals: Optional[list] = None

    class Config:
        from_attributes = True

class ApplicantStatusUpdate(BaseModel):
    """Schema for updating an applicant's status in the hiring workflow."""
    status: str  # new | knockout | rejected | shortlisted | review | interviewed | hired

class BulkApplicantStatusUpdate(BaseModel):
    """Schema for bulk updating multiple applicants' statuses."""
    applicant_ids: List[int]
    status: str

class ApplicantDetailResponse(ApplicantResponse):
    """Extended applicant response with full resume text."""
    phone: Optional[str]
    skills: Union[Dict[str, float], List[str]]
    resume_text: str
    cover_letter: Optional[str] = None
    linkedin_url: Optional[str] = None
    portfolio_url: Optional[str] = None
    custom_answers: Optional[List[Dict[str, str]]] = None

    # Full enriched extraction detail (candidate profile view)
    skills_detailed: Optional[list] = None
    extracted_jobs: Optional[list] = None
    extracted_education: Optional[list] = None
    has_contact_info: Optional[bool] = None
    has_clear_job_titles: Optional[bool] = None
    cover_letter_analysis: Optional[dict] = None
    custom_answer_analysis: Optional[list] = None

    class Config:
        from_attributes = True


# ==============================================================================
# PUBLIC CAREERS PORTAL SCHEMAS
# ==============================================================================

class PublicJobResponse(BaseModel):
    """Public-facing job details (no sensitive company data)."""
    id: int
    title: str
    description: str
    required_skills: List[str]
    min_experience: int
    department: Optional[str] = None
    job_type: Optional[str] = None
    work_location_type: Optional[str] = None
    office_location: Optional[str] = None
    show_salary: bool = True
    salary_min: Optional[int] = None
    salary_max: Optional[int] = None
    currency: str = "USD"
    pay_frequency: str = "Annual"
    benefits: Optional[List[str]] = None
    require_cover_letter: bool = False
    require_portfolio: bool = False
    require_linkedin: bool = False
    custom_questions: Optional[List[str]] = None
    application_deadline: Optional[date] = None

    class Config:
        from_attributes = True


class ApplicantCreate(BaseModel):
    name: str
    email: EmailStr
    phone: Optional[str] = None
    linkedin_url: Optional[str] = None
    portfolio_url: Optional[str] = None
    cover_letter: Optional[str] = None
    custom_answers: Optional[List[Dict[str, str]]] = None

class PublicApplicationSubmission(BaseModel):
    """Schema for public application submission (validated before processing)."""
    name: str
    email: EmailStr
    phone: Optional[str] = None
    linkedin_url: Optional[str] = None
    portfolio_url: Optional[str] = None
    cover_letter: Optional[str] = None
    custom_answers: Optional[List[Dict[str, str]]] = None


# ==============================================================================
# PIPELINE SCHEMAS
# ==============================================================================

class PipelineStageUpdate(BaseModel):
    """Payload to move a candidate to a new pipeline stage."""
    stage: str
    note: Optional[str] = None
    notify_candidate: bool = True  # email the candidate about the move

class StageLogResponse(BaseModel):
    """Single entry in the stage-change audit trail."""
    id: int
    from_stage: str
    to_stage: str
    changed_by_recruiter_id: int
    recruiter_email: Optional[str] = None
    note: Optional[str] = None
    changed_at: datetime

    class Config:
        from_attributes = True

class PipelineApplicantResponse(BaseModel):
    """Lightweight applicant representation for the Kanban pipeline view."""
    id: int
    name: str
    email: str
    match_score: Optional[int] = None
    status: Optional[str] = None  # scoring bucket (shortlisted/review/rejected)
    pipeline_stage: str
    stage_updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True

class ApplicantPipelineResponse(ApplicantResponse):
    """Applicant response extended with pipeline fields."""
    pipeline_stage: str
    stage_updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True