from fastapi import FastAPI, Request, UploadFile, File, Form, HTTPException, Depends
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from database import engine, Base, SessionLocal, run_migrations
from routers import jobs, applicants, auth as auth_router
from routers import public as public_router  # Public careers portal
from routers import pipeline as pipeline_router  # Hiring pipeline stage management
from services.pdf_parser import extract_text_from_pdf
from services.ai_engine import (
    extract_candidate_facts,
    extract_jd_requirements,
    normalize_job_requirements,
    is_ai_available,
    get_ai_unavailable_reason,
    AIServiceUnavailableError,
)
from sqlalchemy import text
from services.job_tracker import job_tracker, JobStatus
from services.auth import get_current_user
from middleware import SecurityLoggingMiddleware, SecurityHeadersMiddleware
import models
import zipfile
import io
import os
import base64
import asyncio
import re
import uuid
import tempfile
import shutil
from typing import Dict, Any, List
from services.tasks import process_resume

# Create Tables + run safe schema migrations
Base.metadata.create_all(bind=engine)
run_migrations()

app = FastAPI(
    title="AI Recruiter ATS",
    description="Multi-tenant ATS with AI-powered resume screening",
    version="1.0.0"
)

JOB_TRACKER_CLEANUP_INTERVAL_SECONDS = int(os.getenv("JOB_TRACKER_CLEANUP_INTERVAL_SECONDS", "1800"))
JOB_TRACKER_MAX_AGE_HOURS = int(os.getenv("JOB_TRACKER_MAX_AGE_HOURS", "24"))
_job_tracker_cleanup_task: asyncio.Task | None = None
TEMP_RESUME_DIR = os.getenv("ATS_RESUME_TMP_DIR", os.path.join(tempfile.gettempdir(), "ats_resumes"))


async def _job_tracker_cleanup_loop():
    while True:
        await asyncio.sleep(JOB_TRACKER_CLEANUP_INTERVAL_SECONDS)
        removed = job_tracker.cleanup_old_jobs(max_age_hours=JOB_TRACKER_MAX_AGE_HOURS)
        if removed:
            print(f"🧹 Job tracker cleanup removed {removed} stale jobs")
"""
When FastAPI starts, a cleanup loop runs in the background.
It only starts one instance of the cleanup loop.
The loop runs asynchronously, so it doesn’t block handling API requests.
"""

@app.on_event("startup")
async def _startup_background_tasks():
    global _job_tracker_cleanup_task
    if _job_tracker_cleanup_task is None:
        _job_tracker_cleanup_task = asyncio.create_task(_job_tracker_cleanup_loop())

"""
This code automatically starts a background cleanup task when FastAPI launches.

The task runs forever asynchronously without blocking API requests.

Old jobs/resumes are cleaned periodically based on your earlier JOB_TRACKER_MAX_AGE_HOURS and JOB_TRACKER_CLEANUP_INTERVAL_SECONDS settings.
"""
@app.on_event("shutdown")
async def _shutdown_background_tasks():
    global _job_tracker_cleanup_task
    if _job_tracker_cleanup_task is not None:
        _job_tracker_cleanup_task.cancel()
        try:
            await _job_tracker_cleanup_task
        except asyncio.CancelledError:
            pass
        _job_tracker_cleanup_task = None

# Add security middleware
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(SecurityLoggingMiddleware)

# CORS configuration. The bundled console/login are same-origin so they need
# no CORS. Set CORS_ORIGINS (comma-separated) only if you host a separate
# frontend domain, e.g. CORS_ORIGINS="https://app.yourdomain.com".
_cors_env = os.getenv("CORS_ORIGINS", "http://localhost:8001")
_cors_origins = [o.strip() for o in _cors_env.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static assets and templates — all served from the single frontend/ folder
# /static/js/auth.js, /static/css/... etc. map to frontend/static/...
app.mount("/static", StaticFiles(directory="frontend/static"), name="static")
templates = Jinja2Templates(directory="frontend")


def render_template(request: Request, template_name: str, context: Dict[str, Any] | None = None):
    """Render templates across Starlette/FastAPI signature differences."""
    page_context = context or {}
    try:
        # Newer call style
        return templates.TemplateResponse(
            request=request,
            name=template_name,
            context=page_context,
        )
    except TypeError:
        # Older call style expects request inside context
        legacy_context = {"request": request, **page_context}
        return templates.TemplateResponse(template_name, legacy_context)

# Include Routers
app.include_router(public_router.router)  # Public careers portal (must be first, no auth)
app.include_router(auth_router.router)
app.include_router(jobs.router)
app.include_router(applicants.router)
app.include_router(pipeline_router.router)  # Hiring pipeline management

# Product-expansion routers (July 2026)
from routers import collaboration as collaboration_router
from routers import interviews as interviews_router
from routers import offers as offers_router
from routers import talent_pool as talent_pool_router
from routers import bulk as bulk_router
from routers import exports as exports_router
from routers import admin_tools as admin_tools_router

app.include_router(collaboration_router.router)  # team, comments, scorecards
app.include_router(interviews_router.router)     # interview scheduling
app.include_router(offers_router.router)         # offer stage
app.include_router(talent_pool_router.router)    # cross-job candidate search
app.include_router(bulk_router.router)           # bulk actions
app.include_router(exports_router.router)        # CSV exports
app.include_router(admin_tools_router.router)    # audit log + email templates



# NOTE: the former /admin/nuke-jobs-table endpoint has been removed. It was
# an UNAUTHENTICATED GET that dropped the jobs table for every tenant —
# anyone with the URL could destroy all customer data. Destructive schema
# operations belong in Alembic migrations run from a shell, never in an
# HTTP endpoint.


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return render_template(request, "index.html")

@app.get("/classic", response_class=HTMLResponse)
def classic_dashboard(request: Request):
    """The original single-file dashboard (kept during the UI migration)."""
    return render_template(request, "index.html")

@app.get("/console", response_class=HTMLResponse)
def console_page(request: Request):
    """Revamped recruiter console (talent pool, interviews, offers, team, audit)."""
    return render_template(request, "console.html")

@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return render_template(request, "login_v2.html")

@app.get("/login/classic", response_class=HTMLResponse)
def login_classic(request: Request):
    return render_template(request, "login.html")

@app.get("/apply/{job_id}", response_class=HTMLResponse)
def apply_page(request: Request, job_id: int):
    """Public careers portal application page."""
    return render_template(request, "apply.html", {"job_id": job_id})

@app.get("/api/health")
def health():
    ai_ready = is_ai_available()
    return {
        "status": "ok",
        "ai_ready": ai_ready,
        "ai_reason": None if ai_ready else get_ai_unavailable_reason(),
    }

@app.post("/api/bulk-screen")
async def bulk_screen_resumes(
    resumes_zip: UploadFile = File(...),
    job_title: str = Form(default=""),
    job_description: str = Form(...),
    min_experience: float = Form(...),
    required_skills: str = Form(...),
    current_user: models.User = Depends(get_current_user),
):
    """
    Upload a ZIP of resume PDFs for bulk screening.
    Returns a tracking job_id for polling progress.
    """
    try:
        if not is_ai_available():
            raise HTTPException(
                status_code=503,
                detail=(
                    "AI extraction service is currently unavailable. "
                    "Please retry after AI connectivity is restored."
                ),
            )

        os.makedirs(TEMP_RESUME_DIR, exist_ok=True)

        zip_saved_path = os.path.join(TEMP_RESUME_DIR, f"{uuid.uuid4()}.zip")
        with open(zip_saved_path, "wb") as out_file:
            shutil.copyfileobj(resumes_zip.file, out_file)

        try:
            with zipfile.ZipFile(zip_saved_path) as zip_file:
                pdf_files = [f for f in zip_file.namelist() if f.lower().endswith(".pdf")]
                if not pdf_files:
                    raise HTTPException(status_code=400, detail="No PDF files found in ZIP")
                total_resumes = len(pdf_files)
        except zipfile.BadZipFile:
            raise HTTPException(status_code=400, detail="Invalid ZIP file")

        skills_list = [s.strip() for s in required_skills.split(",") if s.strip()]
        normalized_min_experience = int(min_experience)
        extracted_requirements = await extract_jd_requirements(job_description)
        normalized_requirements = normalize_job_requirements(extracted_requirements)

        effective_skills = normalized_requirements["must_have_skills"] or skills_list
        extracted_min_exp = normalized_requirements["minimum_years_experience"]
        effective_min_experience = int(extracted_min_exp) if extracted_min_exp > 0 else normalized_min_experience

        clean_title = job_title.strip() or f"Screening — {total_resumes} resumes"

        db = SessionLocal()
        try:
            db_job = models.Job(
                title=clean_title,
                description=job_description,
                min_experience=effective_min_experience,
                required_skills=effective_skills,
                jd_requirements=normalized_requirements,
                is_active=True,
                company_id=current_user.company_id,
            )
            db.add(db_job)
            db.commit()
            db.refresh(db_job)
            db_job_id = db_job.id
        finally:
            db.close()

        tracking_job_id = str(uuid.uuid4())

        # Link tracking ID back to the DB job row immediately
        db2 = SessionLocal()
        try:
            db2.query(models.Job).filter(models.Job.id == db_job_id).update(
                {
                    "tracking_id": tracking_job_id,
                    "total_resumes": total_resumes,
                    "processed_resumes": 0,
                    "status": "processing",
                }
            )
            db2.commit()
        finally:
            db2.close()

        try:
            with zipfile.ZipFile(zip_saved_path) as zip_file:
                for member in pdf_files:
                    pdf_bytes = zip_file.read(member)
                    resume_b64 = base64.b64encode(pdf_bytes).decode("ascii")
                    process_resume.delay(
                        resume_b64=resume_b64,
                        job_id=db_job_id,
                        company_id=current_user.company_id,
                    )
        finally:
            if os.path.exists(zip_saved_path):
                os.remove(zip_saved_path)
        
        return JSONResponse({
            "job_id": tracking_job_id,
            "db_job_id": db_job_id,
            "total_resumes": total_resumes,
            "min_experience": effective_min_experience,
            "required_skills": effective_skills,
            "job_requirements": normalized_requirements,
            "message": "Processing started. Use the job_id to check progress."
        })
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ bulk_screen_resumes failed: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error while starting bulk screening")


@app.get("/api/job/{job_id}")
def get_job_status(
    job_id: str,
    current_user: models.User = Depends(get_current_user)
):
    """
    Check the status of a bulk processing job.
    SECURED: Enforces tenant isolation - users can only view their company's jobs.
    """
    # Retrieve job with company_id verification
    job = job_tracker.get_job(job_id, company_id=current_user.company_id)

    if job:
        return JSONResponse(job)

    # Server-restart fallback: read persisted state from DB
    db = SessionLocal()
    try:
        db_job = (
            db.query(models.Job)
            .filter(
                models.Job.tracking_id == job_id,
                models.Job.company_id == current_user.company_id,
            )
            .first()
        )
        if not db_job:
            raise HTTPException(
                status_code=403,
                detail="Not authorized to view this job or job not found",
            )
        return JSONResponse({
            "id": db_job.tracking_id,
            "status": db_job.status or "completed",
            "total_resumes": db_job.total_resumes or 0,
            "processed": db_job.processed_resumes if db_job.processed_resumes is not None else (db_job.results or {}).get("total_processed", 0),
            "created_at": db_job.created_at.isoformat() if db_job.created_at else None,
            "results": db_job.results,
            "error": None,
        })
    finally:
        db.close()


@app.get("/api/jobs")
def list_bulk_jobs(
    current_user: models.User = Depends(get_current_user)
):
    """
    List all bulk processing jobs for the authenticated user's company.
    SECURED: Enforces tenant isolation.
    """
    # Primary source: DB (survives server restarts)
    db = SessionLocal()
    try:
        db_jobs = (
            db.query(models.Job)
            .filter(models.Job.company_id == current_user.company_id)
            .order_by(models.Job.created_at.desc())
            .all()
        )
        jobs_list = [
            {
                "id": j.tracking_id,
                "db_job_id": j.id,
                "title": j.title,
                "status": j.status,
                "total_resumes": j.total_resumes,
                "created_at": j.created_at.isoformat() if j.created_at else None,
                "results": j.results,
                "company_id": j.company_id,
            }
            for j in db_jobs
            if j.tracking_id  # only rows that were created via bulk-screen
        ]
        return JSONResponse({"jobs": jobs_list, "total": len(jobs_list)})
    finally:
        db.close()