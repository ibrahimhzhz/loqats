"""
routers/offers.py — Offer stage.

  POST  /api/applicants/{id}/offers          create a draft offer
  GET   /api/applicants/{id}/offers          list a candidate's offers
  POST  /api/offers/{offer_id}/send          send the offer (emails candidate)
  PATCH /api/offers/{offer_id}/respond       record accepted / declined
  GET   /api/offers                          all offers for the company
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from datetime import datetime, date
from typing import Optional

from database import get_db
import models
from services.auth import get_current_user
from services import audit, email as email_service

router = APIRouter(prefix="/api", tags=["Offers"])

_VALID_RESPONSE = {"accepted", "declined", "rescinded"}


class OfferCreate(BaseModel):
    job_title: Optional[str] = None
    salary_amount: Optional[int] = None
    currency: Optional[str] = "USD"
    salary_frequency: Optional[str] = "Annual"
    start_date: Optional[date] = None
    expiry_date: Optional[date] = None
    letter_body: Optional[str] = None
    notes: Optional[str] = None


class OfferRespond(BaseModel):
    status: str  # accepted | declined | rescinded


def _get_applicant(db, applicant_id, company_id) -> models.Applicant:
    a = db.query(models.Applicant).filter(
        models.Applicant.id == applicant_id,
        models.Applicant.company_id == company_id,
    ).first()
    if not a:
        raise HTTPException(status_code=404, detail="Applicant not found.")
    return a


def _default_letter(applicant, job_title, offer) -> str:
    salary = ""
    if offer.salary_amount:
        salary = f"\nCompensation: {offer.currency} {offer.salary_amount:,} ({offer.salary_frequency})."
    start = f"\nStart date: {offer.start_date.isoformat()}." if offer.start_date else ""
    return (
        f"Dear {applicant.name or 'Candidate'},\n\n"
        f"We are pleased to offer you the position of {job_title}."
        f"{salary}{start}\n\n"
        "We were impressed by your background and look forward to having you "
        "on the team. Please review the details and let us know if you have "
        "any questions."
    )


def _serialize(o: models.Offer) -> dict:
    return {
        "id": o.id,
        "applicant_id": o.applicant_id,
        "job_id": o.job_id,
        "job_title": o.job_title,
        "salary_amount": o.salary_amount,
        "currency": o.currency,
        "salary_frequency": o.salary_frequency,
        "start_date": o.start_date.isoformat() if o.start_date else None,
        "expiry_date": o.expiry_date.isoformat() if o.expiry_date else None,
        "status": o.status,
        "letter_body": o.letter_body,
        "notes": o.notes,
        "sent_at": o.sent_at.isoformat() if o.sent_at else None,
        "responded_at": o.responded_at.isoformat() if o.responded_at else None,
    }


@router.post("/applicants/{applicant_id}/offers", status_code=201)
def create_offer(
    applicant_id: int,
    payload: OfferCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    applicant = _get_applicant(db, applicant_id, current_user.company_id)
    job = db.query(models.Job).filter(models.Job.id == applicant.job_id).first()
    job_title = payload.job_title or (job.title if job else "the role")

    offer = models.Offer(
        company_id=current_user.company_id,
        applicant_id=applicant_id,
        job_id=applicant.job_id,
        created_by_id=current_user.id,
        job_title=job_title,
        salary_amount=payload.salary_amount,
        currency=payload.currency,
        salary_frequency=payload.salary_frequency,
        start_date=payload.start_date,
        expiry_date=payload.expiry_date,
        notes=payload.notes,
        status="draft",
    )
    offer.letter_body = payload.letter_body or _default_letter(applicant, job_title, offer)
    db.add(offer)
    db.commit()
    db.refresh(offer)

    audit.record(db, current_user.company_id, "offer.created", actor=current_user,
                 entity_type="applicant", entity_id=applicant_id,
                 summary=f"Draft offer for {job_title}")
    return _serialize(offer)


@router.get("/applicants/{applicant_id}/offers")
def list_offers(
    applicant_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    _get_applicant(db, applicant_id, current_user.company_id)
    rows = db.query(models.Offer).filter(
        models.Offer.applicant_id == applicant_id,
        models.Offer.company_id == current_user.company_id,
    ).order_by(models.Offer.created_at.desc()).all()
    return {"offers": [_serialize(o) for o in rows]}


@router.post("/offers/{offer_id}/send")
def send_offer(
    offer_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    offer = db.query(models.Offer).filter(
        models.Offer.id == offer_id,
        models.Offer.company_id == current_user.company_id,
    ).first()
    if not offer:
        raise HTTPException(status_code=404, detail="Offer not found.")
    if offer.status not in {"draft", "sent"}:
        raise HTTPException(status_code=400, detail=f"Can't send an offer that is {offer.status}.")

    applicant = db.query(models.Applicant).filter(models.Applicant.id == offer.applicant_id).first()
    company = db.query(models.Company).filter(models.Company.id == current_user.company_id).first()

    offer.status = "sent"
    offer.sent_at = datetime.utcnow()
    db.commit()

    if applicant:
        email_service.send_templated_email(
            db, current_user.company_id, applicant.email, "offer_sent",
            fields={
                "candidate_name": applicant.name or "there",
                "job_title": offer.job_title or "the role",
                "company_name": company.name if company else "our team",
                "offer_body": offer.letter_body or "",
            },
            applicant_id=applicant.id,
        )
        # Advance pipeline to Offer when relevant
        if applicant.pipeline_stage not in {"Hired", "Rejected"}:
            applicant.pipeline_stage = "Offer"
            db.commit()

    audit.record(db, current_user.company_id, "offer.sent", actor=current_user,
                 entity_type="applicant", entity_id=offer.applicant_id,
                 summary=f"Offer sent for {offer.job_title}")
    return _serialize(offer)


@router.patch("/offers/{offer_id}/respond")
def respond_offer(
    offer_id: int,
    payload: OfferRespond,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    if payload.status not in _VALID_RESPONSE:
        raise HTTPException(status_code=400, detail=f"status must be one of {sorted(_VALID_RESPONSE)}")
    offer = db.query(models.Offer).filter(
        models.Offer.id == offer_id,
        models.Offer.company_id == current_user.company_id,
    ).first()
    if not offer:
        raise HTTPException(status_code=404, detail="Offer not found.")

    offer.status = payload.status
    offer.responded_at = datetime.utcnow()
    db.commit()

    # Reflect on the candidate's pipeline stage
    applicant = db.query(models.Applicant).filter(models.Applicant.id == offer.applicant_id).first()
    if applicant:
        if payload.status == "accepted":
            applicant.pipeline_stage = "Hired"
            applicant.status = "hired"
        elif payload.status in {"declined", "rescinded"}:
            applicant.pipeline_stage = "Rejected"
        db.commit()

    audit.record(db, current_user.company_id, f"offer.{payload.status}", actor=current_user,
                 entity_type="applicant", entity_id=offer.applicant_id,
                 summary=f"Offer {payload.status} for {offer.job_title}")
    return _serialize(offer)


@router.get("/offers")
def all_offers(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    rows = db.query(models.Offer).filter(
        models.Offer.company_id == current_user.company_id,
    ).order_by(models.Offer.created_at.desc()).limit(500).all()
    return {"offers": [_serialize(o) for o in rows]}
