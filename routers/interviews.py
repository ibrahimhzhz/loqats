"""
routers/interviews.py — Interview scheduling.

  POST   /api/applicants/{id}/interviews     schedule an interview (emails candidate)
  GET    /api/applicants/{id}/interviews     list a candidate's interviews
  GET    /api/interviews                     upcoming interviews across all jobs
  PATCH  /api/interviews/{interview_id}       update status / reschedule
  GET    /api/interviews/{interview_id}/ics   download calendar invite (.ics)
"""

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from typing import Optional, List
import uuid as _uuid

from database import get_db
import models
from services.auth import get_current_user
from services import audit, email as email_service

router = APIRouter(prefix="/api", tags=["Interviews"])

_VALID_STATUS = {"scheduled", "completed", "cancelled", "no_show"}


class InterviewCreate(BaseModel):
    title: str = "Interview"
    stage: Optional[str] = None
    start_time: datetime
    end_time: Optional[datetime] = None
    timezone_name: Optional[str] = "UTC"
    location: Optional[str] = None
    meeting_link: Optional[str] = None
    interviewer_ids: Optional[List[int]] = None
    notes: Optional[str] = None
    notify_candidate: bool = True


class InterviewUpdate(BaseModel):
    status: Optional[str] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    location: Optional[str] = None
    meeting_link: Optional[str] = None
    notes: Optional[str] = None
    notify_candidate: bool = False


def _get_applicant(db, applicant_id, company_id) -> models.Applicant:
    a = db.query(models.Applicant).filter(
        models.Applicant.id == applicant_id,
        models.Applicant.company_id == company_id,
    ).first()
    if not a:
        raise HTTPException(status_code=404, detail="Applicant not found.")
    return a


def _serialize(iv: models.Interview) -> dict:
    return {
        "id": iv.id,
        "applicant_id": iv.applicant_id,
        "job_id": iv.job_id,
        "title": iv.title,
        "stage": iv.stage,
        "start_time": iv.start_time.isoformat() if iv.start_time else None,
        "end_time": iv.end_time.isoformat() if iv.end_time else None,
        "timezone_name": iv.timezone_name,
        "location": iv.location,
        "meeting_link": iv.meeting_link,
        "interviewer_ids": iv.interviewer_ids or [],
        "status": iv.status,
        "notes": iv.notes,
    }


def _ics_for(iv: models.Interview, candidate_name: str, company_name: str) -> str:
    start = iv.start_time or datetime.utcnow()
    end = iv.end_time or (start + timedelta(hours=1))
    fmt = lambda d: d.strftime("%Y%m%dT%H%M%SZ")
    loc = iv.meeting_link or iv.location or ""
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//LoqATS//Interview//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:REQUEST",
        "BEGIN:VEVENT",
        f"UID:{iv.id}-{_uuid.uuid4().hex}@loqats",
        f"DTSTAMP:{fmt(datetime.utcnow())}",
        f"DTSTART:{fmt(start)}",
        f"DTEND:{fmt(end)}",
        f"SUMMARY:{iv.title} — {candidate_name} ({company_name})",
        f"DESCRIPTION:{(iv.notes or '').replace(chr(10), ' ')}",
        f"LOCATION:{loc}",
        "STATUS:CONFIRMED",
        "END:VEVENT",
        "END:VCALENDAR",
    ]
    return "\r\n".join(lines)


def _notify(db, applicant, iv, company):
    when = iv.start_time.strftime("%A, %d %B %Y at %H:%M UTC") if iv.start_time else "TBD"
    loc_line = ""
    if iv.meeting_link:
        loc_line = f"Join here: {iv.meeting_link}"
    elif iv.location:
        loc_line = f"Location: {iv.location}"
    email_service.send_templated_email(
        db, company.id, applicant.email, "interview_scheduled",
        fields={
            "candidate_name": applicant.name or "there",
            "job_title": _job_title(db, iv.job_id),
            "company_name": company.name if company else "our team",
            "interview_title": iv.title,
            "interview_when": when,
            "interview_location_line": loc_line,
        },
        applicant_id=applicant.id,
    )


def _job_title(db, job_id) -> str:
    job = db.query(models.Job).filter(models.Job.id == job_id).first()
    return job.title if job and job.title else "the role"


@router.post("/applicants/{applicant_id}/interviews", status_code=201)
def schedule_interview(
    applicant_id: int,
    payload: InterviewCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    applicant = _get_applicant(db, applicant_id, current_user.company_id)

    iv = models.Interview(
        company_id=current_user.company_id,
        applicant_id=applicant_id,
        job_id=applicant.job_id,
        scheduled_by_id=current_user.id,
        title=payload.title,
        stage=payload.stage,
        start_time=payload.start_time,
        end_time=payload.end_time,
        timezone_name=payload.timezone_name,
        location=payload.location,
        meeting_link=payload.meeting_link,
        interviewer_ids=payload.interviewer_ids,
        notes=payload.notes,
        status="scheduled",
    )
    db.add(iv)
    db.commit()
    db.refresh(iv)

    company = db.query(models.Company).filter(models.Company.id == current_user.company_id).first()
    audit.record(db, current_user.company_id, "interview.scheduled", actor=current_user,
                 entity_type="applicant", entity_id=applicant_id,
                 summary=f"{iv.title} on {iv.start_time.isoformat() if iv.start_time else 'TBD'}")

    if payload.notify_candidate:
        _notify(db, applicant, iv, company)

    return _serialize(iv)


@router.get("/applicants/{applicant_id}/interviews")
def list_for_applicant(
    applicant_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    _get_applicant(db, applicant_id, current_user.company_id)
    rows = db.query(models.Interview).filter(
        models.Interview.applicant_id == applicant_id,
        models.Interview.company_id == current_user.company_id,
    ).order_by(models.Interview.start_time.desc()).all()
    return {"interviews": [_serialize(r) for r in rows]}


@router.get("/interviews")
def upcoming_interviews(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    rows = db.query(models.Interview).filter(
        models.Interview.company_id == current_user.company_id,
        models.Interview.status == "scheduled",
        models.Interview.start_time >= datetime.utcnow() - timedelta(hours=12),
    ).order_by(models.Interview.start_time.asc()).limit(200).all()

    # attach candidate name for the calendar UI
    applicant_ids = {r.applicant_id for r in rows}
    names = {
        a.id: a.name for a in db.query(models.Applicant.id, models.Applicant.name)
        .filter(models.Applicant.id.in_(applicant_ids)).all()
    } if applicant_ids else {}

    out = []
    for r in rows:
        d = _serialize(r)
        d["candidate_name"] = names.get(r.applicant_id, "Candidate")
        out.append(d)
    return {"interviews": out}


@router.patch("/interviews/{interview_id}")
def update_interview(
    interview_id: int,
    payload: InterviewUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    iv = db.query(models.Interview).filter(
        models.Interview.id == interview_id,
        models.Interview.company_id == current_user.company_id,
    ).first()
    if not iv:
        raise HTTPException(status_code=404, detail="Interview not found.")

    if payload.status is not None:
        if payload.status not in _VALID_STATUS:
            raise HTTPException(status_code=400, detail=f"status must be one of {sorted(_VALID_STATUS)}")
        iv.status = payload.status
    if payload.start_time is not None:
        iv.start_time = payload.start_time
    if payload.end_time is not None:
        iv.end_time = payload.end_time
    if payload.location is not None:
        iv.location = payload.location
    if payload.meeting_link is not None:
        iv.meeting_link = payload.meeting_link
    if payload.notes is not None:
        iv.notes = payload.notes
    db.commit()
    db.refresh(iv)

    audit.record(db, current_user.company_id, "interview.updated", actor=current_user,
                 entity_type="applicant", entity_id=iv.applicant_id,
                 summary=f"Interview {iv.id} → {iv.status}")

    if payload.notify_candidate:
        applicant = db.query(models.Applicant).filter(models.Applicant.id == iv.applicant_id).first()
        company = db.query(models.Company).filter(models.Company.id == current_user.company_id).first()
        if applicant:
            _notify(db, applicant, iv, company)

    return _serialize(iv)


@router.get("/interviews/{interview_id}/ics")
def download_ics(
    interview_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    iv = db.query(models.Interview).filter(
        models.Interview.id == interview_id,
        models.Interview.company_id == current_user.company_id,
    ).first()
    if not iv:
        raise HTTPException(status_code=404, detail="Interview not found.")
    applicant = db.query(models.Applicant).filter(models.Applicant.id == iv.applicant_id).first()
    company = db.query(models.Company).filter(models.Company.id == current_user.company_id).first()
    ics = _ics_for(iv, applicant.name if applicant else "Candidate",
                   company.name if company else "Company")
    return Response(
        content=ics,
        media_type="text/calendar",
        headers={"Content-Disposition": f'attachment; filename="interview-{iv.id}.ics"'},
    )
