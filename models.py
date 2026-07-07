from sqlalchemy import Column, Integer, String, Text, ForeignKey, Float, Boolean, JSON, DateTime, Date, LargeBinary, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from database import Base


# ==============================================================================
# PIPELINE STAGE DEFINITIONS
# ==============================================================================

PIPELINE_STAGES = [
    "Applied",
    "Recruiter Screen",
    "Hiring Manager Review",
    "Interview",
    "Offer",
    "Hired",
    "Rejected",
]

TERMINAL_STAGES = {"Hired", "Rejected"}


# ==============================================================================
# TENANT MODEL
# ==============================================================================

class Company(Base):
    """Top-level tenant. Every piece of data belongs to a Company."""
    __tablename__ = "companies"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True, nullable=False)
    subscription_tier = Column(String, default="free")  # free | pro | enterprise
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    users = relationship("User", back_populates="company")
    jobs = relationship("Job", back_populates="company")


# ==============================================================================
# USER MODEL
# ==============================================================================

class User(Base):
    """Authenticated user scoped to a Company tenant."""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    role = Column(String, default="recruiter")  # recruiter | admin
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    company = relationship("Company", back_populates="users")


# ==============================================================================
# CORE ATS MODELS (now tenant-scoped via company_id)
# ==============================================================================

class Job(Base):
    __tablename__ = "jobs"

    id = Column(Integer, primary_key=True, index=True)
    # Multi-tenancy: every job belongs to exactly one company
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False, index=True)
    # Bulk-screen tracking
    tracking_id = Column(String, unique=True, nullable=True, index=True)  # UUID from job_tracker
    status     = Column(String, nullable=False, default="Draft")           # Draft|Live
    total_resumes = Column(Integer, nullable=True)
    processed_resumes = Column(Integer, nullable=True, default=0)
    results    = Column(JSON, nullable=True)                               # full results dict persisted on completion
    title = Column(String, index=True)
    description = Column(Text)
    min_experience = Column(Integer)
    required_skills = Column(JSON)  # List of strings ["Python", "SQL"]
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Public Careers Portal Analytics
    views = Column(Integer, default=0)  # Track page views on public portal
    application_count = Column(Integer, default=0)  # Track submitted applications
    form_config = Column(JSON, nullable=True)  # Custom form configuration
    
    # Level 1: Structured JD requirements baseline
    jd_requirements = Column(JSON, nullable=True)  # {must_have_skills, minimum_years_experience, education_requirement, offers_visa_sponsorship}
    required_skill_embeddings = Column(JSON, nullable=True)  # {skill_name: [float, ...]}

    # Redesigned job posting form fields
    department = Column(String, nullable=True)
    job_type = Column(String, nullable=True)
    work_location_type = Column(String, nullable=True)
    office_location = Column(String, nullable=True)
    openings = Column(Integer, nullable=False, default=1)
    salary_min = Column(Integer, nullable=True)
    salary_max = Column(Integer, nullable=True)
    currency = Column(String, nullable=False, default="USD")
    pay_frequency = Column(String, nullable=False, default="Annual")
    show_salary = Column(Boolean, nullable=False, default=True)
    equity_bonus = Column(String, nullable=True)
    nice_to_have_skills = Column(JSON, nullable=True)
    benefits = Column(JSON, nullable=True)
    require_cover_letter = Column(Boolean, nullable=False, default=False)
    require_portfolio = Column(Boolean, nullable=False, default=False)
    require_linkedin = Column(Boolean, nullable=False, default=False)
    custom_questions = Column(JSON, nullable=True)
    hiring_manager = Column(String, nullable=True)
    target_hire_date = Column(Date, nullable=True)
    application_deadline = Column(Date, nullable=True)
    visibility = Column(String, nullable=False, default="Public")

    company = relationship("Company", back_populates="jobs")
    applicants = relationship("Applicant", back_populates="job", cascade="all, delete-orphan")


class Applicant(Base):
    __tablename__ = "applicants"
    __table_args__ = (
        UniqueConstraint("job_id", "email", name="uq_applicants_job_email"),
    )

    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=False)
    # Denormalized for fast tenant-scoped queries without a JOIN on jobs
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False, index=True)
    name = Column(String)
    email = Column(String)
    phone = Column(String, nullable=True)
    resume_text = Column(Text)  # Raw text from PDF
    resume_pdf = Column(LargeBinary, nullable=True)  # Legacy: PDF stored in-DB (fallback only)
    resume_file_key = Column(String, nullable=True)  # Object-storage key (preferred)

    # AI Extracted Data
    years_experience = Column(Integer)
    skills = Column(JSON)  # Extracted skills ["Python", "FastAPI"]
    skill_embeddings = Column(JSON, nullable=True)  # {skill_name: [float, ...]}

    # Scoring
    match_score = Column(Integer)
    summary = Column(Text)  # AI reasoning
    breakdown = Column(JSON, nullable=True)            # {skill_depth, title_match, experience, impact}
    status = Column(String, default="new")             # new | knockout | rejected | shortlisted | review
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Public Careers Portal Fields
    cover_letter = Column(Text, nullable=True)
    linkedin_url = Column(String, nullable=True)
    portfolio_url = Column(String, nullable=True)
    custom_answers = Column(JSON, nullable=True)  # [{question: str, answer: str}]

    # Enriched extraction storage
    skills_detailed = Column(JSON, nullable=True)        # [{name, years_used, last_used_year, job_index}]
    extracted_jobs = Column(JSON, nullable=True)          # [{title, company, start_year, end_year, is_current, domain, work_type}]
    extracted_education = Column(JSON, nullable=True)     # [{degree, field_of_study, institution, year}]

    # Candidate signal fields (from extraction)
    has_measurable_impact = Column(Boolean, nullable=True, default=None)
    has_contact_info = Column(Boolean, nullable=True, default=None)
    has_clear_job_titles = Column(Boolean, nullable=True, default=None)
    employment_gaps = Column(Boolean, nullable=True, default=None)
    average_tenure_years = Column(Float, nullable=True, default=None)
    extractable_text = Column(Boolean, nullable=True, default=True)

    # Cover letter and custom answer analysis from extraction
    cover_letter_analysis = Column(JSON, nullable=True)  # {word_count, mentions_role_title, skills_mentioned, ...}
    custom_answer_analysis = Column(JSON, nullable=True)  # [{question_index, relevance, detail_level, flags}]

    # Score breakdown storage (populated by scoring engine)
    score_breakdown = Column(JSON, nullable=True)        # {experience, skills, education, role_level, application_quality}
    knockout_flags = Column(JSON, nullable=True)          # [{type, severity, reason}]
    candidate_signals = Column(JSON, nullable=True)       # [{type, level, color}]

    # Hiring Pipeline
    pipeline_stage = Column(String, default="Applied", nullable=False)  # See PIPELINE_STAGES
    stage_updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    job = relationship("Job", back_populates="applicants")
    stage_history = relationship("ApplicantStageLog", back_populates="applicant", cascade="all, delete-orphan")


# ==============================================================================
# PIPELINE AUDIT LOG
# ==============================================================================

class ApplicantStageLog(Base):
    """Immutable audit log — rows are never updated or deleted, only inserted."""
    __tablename__ = "applicant_stage_log"

    id = Column(Integer, primary_key=True, index=True)
    applicant_id = Column(Integer, ForeignKey("applicants.id"), nullable=False, index=True)
    from_stage = Column(String, nullable=False)
    to_stage = Column(String, nullable=False)
    changed_by_recruiter_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    note = Column(Text, nullable=True)
    changed_at = Column(DateTime(timezone=True), server_default=func.now())

    applicant = relationship("Applicant", back_populates="stage_history")
    changed_by = relationship("User")

# ==============================================================================
# ============  PRODUCT EXPANSION MODELS (July 2026)  ==========================
# Team collaboration, interviews, offers, audit trail, notifications, and
# object-storage-backed resumes. All tenant-scoped via company_id.
# ==============================================================================

VALID_USER_ROLES = {"admin", "recruiter", "hiring_manager", "interviewer", "viewer"}


class Comment(Base):
    """A note left on a candidate by a team member. Supports @mentions."""
    __tablename__ = "comments"

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False, index=True)
    applicant_id = Column(Integer, ForeignKey("applicants.id"), nullable=False, index=True)
    author_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    body = Column(Text, nullable=False)
    mentioned_user_ids = Column(JSON, nullable=True)  # [user_id, ...]
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    author = relationship("User")


class Scorecard(Base):
    """Structured interview feedback for a candidate at a given stage."""
    __tablename__ = "scorecards"

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False, index=True)
    applicant_id = Column(Integer, ForeignKey("applicants.id"), nullable=False, index=True)
    interview_id = Column(Integer, ForeignKey("interviews.id"), nullable=True)
    reviewer_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    stage = Column(String, nullable=True)
    # recommendation: strong_yes | yes | no | strong_no
    recommendation = Column(String, nullable=False, default="no")
    overall_rating = Column(Integer, nullable=True)  # 1-4
    criteria = Column(JSON, nullable=True)  # [{name, rating(1-4), notes}]
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    reviewer = relationship("User")


class Interview(Base):
    """A scheduled interview for a candidate."""
    __tablename__ = "interviews"

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False, index=True)
    applicant_id = Column(Integer, ForeignKey("applicants.id"), nullable=False, index=True)
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=False)
    scheduled_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    title = Column(String, nullable=False, default="Interview")
    stage = Column(String, nullable=True)  # pipeline stage this interview belongs to
    start_time = Column(DateTime(timezone=True), nullable=False)
    end_time = Column(DateTime(timezone=True), nullable=True)
    timezone_name = Column(String, nullable=True, default="UTC")
    location = Column(String, nullable=True)      # physical address
    meeting_link = Column(String, nullable=True)  # video URL
    interviewer_ids = Column(JSON, nullable=True)  # [user_id, ...]
    # status: scheduled | completed | cancelled | no_show
    status = Column(String, nullable=False, default="scheduled")
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    scheduled_by = relationship("User")


class Offer(Base):
    """A job offer extended to a candidate."""
    __tablename__ = "offers"

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False, index=True)
    applicant_id = Column(Integer, ForeignKey("applicants.id"), nullable=False, index=True)
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=False)
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    job_title = Column(String, nullable=True)
    salary_amount = Column(Integer, nullable=True)
    currency = Column(String, nullable=True, default="USD")
    salary_frequency = Column(String, nullable=True, default="Annual")
    start_date = Column(Date, nullable=True)
    expiry_date = Column(Date, nullable=True)
    # status: draft | sent | accepted | declined | rescinded
    status = Column(String, nullable=False, default="draft")
    letter_body = Column(Text, nullable=True)
    notes = Column(Text, nullable=True)
    sent_at = Column(DateTime(timezone=True), nullable=True)
    responded_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    created_by = relationship("User")


class AuditLog(Base):
    """Immutable, append-only record of every consequential action."""
    __tablename__ = "audit_log"

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False, index=True)
    actor_id = Column(Integer, ForeignKey("users.id"), nullable=True)  # null = system
    actor_email = Column(String, nullable=True)   # denormalized for durability
    action = Column(String, nullable=False, index=True)   # e.g. "applicant.stage_changed"
    entity_type = Column(String, nullable=True)   # "applicant" | "job" | "offer" ...
    entity_id = Column(Integer, nullable=True)
    summary = Column(String, nullable=True)       # human-readable one-liner
    meta = Column(JSON, nullable=True)            # structured before/after, extra context
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)


class EmailLog(Base):
    """Record of every notification email attempted."""
    __tablename__ = "email_log"

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=True, index=True)
    applicant_id = Column(Integer, ForeignKey("applicants.id"), nullable=True)
    to_email = Column(String, nullable=False)
    template_key = Column(String, nullable=True)
    subject = Column(String, nullable=True)
    # status: sent | failed | skipped
    status = Column(String, nullable=False, default="sent")
    error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class EmailTemplate(Base):
    """Per-company, per-event customizable email template."""
    __tablename__ = "email_templates"
    __table_args__ = (
        UniqueConstraint("company_id", "key", name="uq_email_template_company_key"),
    )

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False, index=True)
    key = Column(String, nullable=False)  # application_received | stage_changed | ...
    subject = Column(String, nullable=False)
    body = Column(Text, nullable=False)
    enabled = Column(Boolean, nullable=False, default=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
