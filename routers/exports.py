"""
routers/exports.py — CSV exports.

  GET  /api/jobs/{job_id}/export.csv     every candidate on a job
  POST /api/export/candidates.csv        export a specific set of candidate ids

Exports are audit-logged (who exported what, when) because candidate data
leaving the system is exactly the kind of event a compliance review asks about.
"""

import csv
import io
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from typing import List

from database import get_db
import models
from services.auth import get_current_user
from services import audit

router = APIRouter(prefix="/api", tags=["Exports"])

_COLUMNS = [
    "id", "name", "email", "phone", "years_experience", "match_score",
    "status", "pipeline_stage", "top_skills", "job_title", "created_at",
]


def _row(a: models.Applicant, job_title: str) -> list:
    skills = []
    if isinstance(a.skills, dict):
        skills = list(a.skills.keys())
    elif a.skills_detailed:
        skills = [s.get("name") for s in a.skills_detailed if s.get("name")]
    return [
        a.id, a.name or "", a.email or "", a.phone or "",
        a.years_experience or 0, a.match_score or 0,
        a.status or "", a.pipeline_stage or "",
        "; ".join([s for s in skills if s][:15]),
        job_title,
        a.created_at.isoformat() if a.created_at else "",
    ]


def _csv_response(rows: List[list], filename: str) -> Response:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(_COLUMNS)
    writer.writerows(rows)
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/jobs/{job_id}/export.csv")
def export_job(
    job_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    job = db.query(models.Job).filter(
        models.Job.id == job_id,
        models.Job.company_id == current_user.company_id,
    ).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")

    applicants = db.query(models.Applicant).filter(
        models.Applicant.job_id == job_id,
        models.Applicant.company_id == current_user.company_id,
    ).order_by(models.Applicant.match_score.desc()).all()

    rows = [_row(a, job.title or "") for a in applicants]
    audit.record(db, current_user.company_id, "export.job_csv", actor=current_user,
                 entity_type="job", entity_id=job_id,
                 summary=f"Exported {len(rows)} candidates from '{job.title}'")
    safe = (job.title or "job").replace(" ", "_")[:40]
    return _csv_response(rows, f"{safe}_candidates.csv")


class ExportIds(BaseModel):
    applicant_ids: List[int] = Field(min_length=1)


@router.post("/export/candidates.csv")
def export_candidates(
    payload: ExportIds,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    applicants = db.query(models.Applicant).filter(
        models.Applicant.id.in_(payload.applicant_ids),
        models.Applicant.company_id == current_user.company_id,
    ).all()
    if not applicants:
        raise HTTPException(status_code=404, detail="No matching candidates.")

    job_titles = {
        j.id: j.title for j in db.query(models.Job.id, models.Job.title).filter(
            models.Job.company_id == current_user.company_id
        ).all()
    }
    rows = [_row(a, job_titles.get(a.job_id, "")) for a in applicants]
    audit.record(db, current_user.company_id, "export.candidates_csv", actor=current_user,
                 entity_type="applicant", entity_id=None,
                 summary=f"Exported {len(rows)} candidates")
    return _csv_response(rows, "candidates.csv")
