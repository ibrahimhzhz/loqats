"""
routers/bulk.py — Bulk actions on candidates.

  POST /api/bulk/stage      move many candidates to a pipeline stage
  POST /api/bulk/reject     reject many candidates, optionally emailing each
  POST /api/bulk/status     set legacy status on many candidates

All actions are tenant-scoped and audit-logged. Rejection emails go out one
per candidate through the shared template so wording stays consistent.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from datetime import datetime
from typing import List, Optional

from database import get_db
import models
from services.auth import get_current_user
from services import audit, email as email_service

router = APIRouter(prefix="/api/bulk", tags=["Bulk Actions"])

_VALID_STAGES = set(models.PIPELINE_STAGES)
_VALID_STATUS = {"new", "rejected", "shortlisted", "review", "interviewed", "hired", "knockout"}


class BulkStage(BaseModel):
    applicant_ids: List[int] = Field(min_length=1)
    stage: str


class BulkReject(BaseModel):
    applicant_ids: List[int] = Field(min_length=1)
    notify: bool = True


class BulkStatus(BaseModel):
    applicant_ids: List[int] = Field(min_length=1)
    status: str


def _fetch(db, ids, company_id) -> List[models.Applicant]:
    rows = db.query(models.Applicant).filter(
        models.Applicant.id.in_(ids),
        models.Applicant.company_id == company_id,
    ).all()
    if not rows:
        raise HTTPException(status_code=404, detail="No matching candidates found.")
    return rows


@router.post("/stage")
def bulk_stage(
    payload: BulkStage,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    if payload.stage not in _VALID_STAGES:
        raise HTTPException(status_code=400, detail=f"stage must be one of {sorted(_VALID_STAGES)}")
    rows = _fetch(db, payload.applicant_ids, current_user.company_id)
    for a in rows:
        prev = a.pipeline_stage
        a.pipeline_stage = payload.stage
        a.stage_updated_at = datetime.utcnow()
        db.add(models.ApplicantStageLog(
            applicant_id=a.id, from_stage=prev or "Applied", to_stage=payload.stage,
            changed_by_recruiter_id=current_user.id, note="bulk action",
        ))
    db.commit()
    audit.record(db, current_user.company_id, "bulk.stage_changed", actor=current_user,
                 entity_type="applicant", entity_id=None,
                 summary=f"{len(rows)} → {payload.stage}",
                 meta={"applicant_ids": [a.id for a in rows]})
    return {"updated": len(rows), "stage": payload.stage}


@router.post("/reject")
def bulk_reject(
    payload: BulkReject,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    rows = _fetch(db, payload.applicant_ids, current_user.company_id)
    company = db.query(models.Company).filter(models.Company.id == current_user.company_id).first()

    emailed = 0
    for a in rows:
        prev = a.pipeline_stage
        a.pipeline_stage = "Rejected"
        a.status = "rejected"
        a.stage_updated_at = datetime.utcnow()
        db.add(models.ApplicantStageLog(
            applicant_id=a.id, from_stage=prev or "Applied", to_stage="Rejected",
            changed_by_recruiter_id=current_user.id, note="bulk reject",
        ))
    db.commit()

    if payload.notify:
        for a in rows:
            job = db.query(models.Job).filter(models.Job.id == a.job_id).first()
            sent = email_service.send_templated_email(
                db, current_user.company_id, a.email, "rejected",
                fields={
                    "candidate_name": a.name or "there",
                    "job_title": job.title if job else "the role",
                    "company_name": company.name if company else "our team",
                },
                applicant_id=a.id,
            )
            if sent:
                emailed += 1

    audit.record(db, current_user.company_id, "bulk.rejected", actor=current_user,
                 entity_type="applicant", entity_id=None,
                 summary=f"{len(rows)} rejected, {emailed} emailed",
                 meta={"applicant_ids": [a.id for a in rows]})
    return {"rejected": len(rows), "emailed": emailed}


@router.post("/status")
def bulk_status(
    payload: BulkStatus,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    if payload.status not in _VALID_STATUS:
        raise HTTPException(status_code=400, detail=f"status must be one of {sorted(_VALID_STATUS)}")
    rows = _fetch(db, payload.applicant_ids, current_user.company_id)
    for a in rows:
        a.status = payload.status
    db.commit()
    audit.record(db, current_user.company_id, "bulk.status_changed", actor=current_user,
                 entity_type="applicant", entity_id=None,
                 summary=f"{len(rows)} → {payload.status}")
    return {"updated": len(rows), "status": payload.status}
