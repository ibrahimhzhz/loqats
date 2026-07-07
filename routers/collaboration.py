"""
routers/collaboration.py — Team, comments, and scorecards.

Endpoints
  Team
    GET   /api/team                      list teammates (for @mentions & assignment)
    POST  /api/team/invite               admin: add a teammate
    PATCH /api/team/{user_id}/role       admin: change a teammate's role

  Comments (candidate notes + @mentions)
    GET   /api/applicants/{id}/comments
    POST  /api/applicants/{id}/comments
    DELETE /api/comments/{comment_id}

  Scorecards (structured interview feedback)
    GET   /api/applicants/{id}/scorecards
    POST  /api/applicants/{id}/scorecards
"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session
from typing import Optional, List, Any, Dict

from database import get_db
import models
from services.auth import get_current_user, hash_password
from services import audit, email as email_service

router = APIRouter(prefix="/api", tags=["Collaboration"])


# ── helpers ────────────────────────────────────────────────────────────────

def _require_admin(user: models.User):
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Only admins can manage the team.")


def _get_applicant(db: Session, applicant_id: int, company_id: int) -> models.Applicant:
    applicant = db.query(models.Applicant).filter(
        models.Applicant.id == applicant_id,
        models.Applicant.company_id == company_id,
    ).first()
    if not applicant:
        raise HTTPException(status_code=404, detail="Applicant not found.")
    return applicant


def _user_public(u: models.User) -> Dict[str, Any]:
    return {"id": u.id, "email": u.email, "role": u.role}


# ── schemas ────────────────────────────────────────────────────────────────

class TeamInvite(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    role: str = "recruiter"


class RoleUpdate(BaseModel):
    role: str


class CommentCreate(BaseModel):
    body: str = Field(min_length=1)
    mentioned_user_ids: Optional[List[int]] = None


class ScorecardCriterion(BaseModel):
    name: str
    rating: Optional[int] = None  # 1-4
    notes: Optional[str] = None


class ScorecardCreate(BaseModel):
    recommendation: str = Field(description="strong_yes | yes | no | strong_no")
    overall_rating: Optional[int] = None
    stage: Optional[str] = None
    interview_id: Optional[int] = None
    criteria: Optional[List[ScorecardCriterion]] = None
    notes: Optional[str] = None


# ── team ───────────────────────────────────────────────────────────────────

@router.get("/team")
def list_team(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    users = db.query(models.User).filter(
        models.User.company_id == current_user.company_id
    ).order_by(models.User.email).all()
    return {"team": [_user_public(u) for u in users]}


@router.post("/team/invite", status_code=201)
def invite_teammate(
    payload: TeamInvite,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    _require_admin(current_user)
    if payload.role not in models.VALID_USER_ROLES:
        raise HTTPException(status_code=400, detail=f"Invalid role. One of: {sorted(models.VALID_USER_ROLES)}")
    if db.query(models.User).filter(models.User.email == payload.email).first():
        raise HTTPException(status_code=409, detail="That email is already registered.")

    user = models.User(
        company_id=current_user.company_id,
        email=payload.email,
        hashed_password=hash_password(payload.password),
        role=payload.role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    audit.record(db, current_user.company_id, "team.member_added", actor=current_user,
                 entity_type="user", entity_id=user.id,
                 summary=f"Added {user.email} as {user.role}")
    return _user_public(user)


@router.patch("/team/{user_id}/role")
def update_role(
    user_id: int,
    payload: RoleUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    _require_admin(current_user)
    if payload.role not in models.VALID_USER_ROLES:
        raise HTTPException(status_code=400, detail=f"Invalid role. One of: {sorted(models.VALID_USER_ROLES)}")
    user = db.query(models.User).filter(
        models.User.id == user_id,
        models.User.company_id == current_user.company_id,
    ).first()
    if not user:
        raise HTTPException(status_code=404, detail="Teammate not found.")
    if user.id == current_user.id and payload.role != "admin":
        raise HTTPException(status_code=400, detail="You can't remove your own admin role.")
    old = user.role
    user.role = payload.role
    db.commit()
    audit.record(db, current_user.company_id, "team.role_changed", actor=current_user,
                 entity_type="user", entity_id=user.id,
                 summary=f"{user.email}: {old} → {payload.role}")
    return _user_public(user)


# ── comments ───────────────────────────────────────────────────────────────

@router.get("/applicants/{applicant_id}/comments")
def list_comments(
    applicant_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    _get_applicant(db, applicant_id, current_user.company_id)
    rows = db.query(models.Comment).filter(
        models.Comment.applicant_id == applicant_id,
        models.Comment.company_id == current_user.company_id,
    ).order_by(models.Comment.created_at.asc()).all()

    # Resolve author emails for display
    author_ids = {r.author_id for r in rows}
    authors = {
        u.id: u.email for u in db.query(models.User).filter(models.User.id.in_(author_ids)).all()
    } if author_ids else {}

    return {"comments": [
        {
            "id": r.id,
            "body": r.body,
            "author_id": r.author_id,
            "author_email": authors.get(r.author_id, "unknown"),
            "mentioned_user_ids": r.mentioned_user_ids or [],
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]}


@router.post("/applicants/{applicant_id}/comments", status_code=201)
def create_comment(
    applicant_id: int,
    payload: CommentCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    _get_applicant(db, applicant_id, current_user.company_id)

    # Validate mentioned users belong to the same company
    valid_mentions: List[int] = []
    if payload.mentioned_user_ids:
        teammate_ids = {
            u.id for u in db.query(models.User.id).filter(
                models.User.company_id == current_user.company_id,
                models.User.id.in_(payload.mentioned_user_ids),
            ).all()
        }
        valid_mentions = [uid for uid in payload.mentioned_user_ids if uid in teammate_ids]

    comment = models.Comment(
        company_id=current_user.company_id,
        applicant_id=applicant_id,
        author_id=current_user.id,
        body=payload.body,
        mentioned_user_ids=valid_mentions or None,
    )
    db.add(comment)
    db.commit()
    db.refresh(comment)

    audit.record(db, current_user.company_id, "applicant.commented", actor=current_user,
                 entity_type="applicant", entity_id=applicant_id,
                 summary=f"Comment added ({len(valid_mentions)} mention(s))")

    return {
        "id": comment.id,
        "body": comment.body,
        "author_id": comment.author_id,
        "author_email": current_user.email,
        "mentioned_user_ids": valid_mentions,
        "created_at": comment.created_at.isoformat() if comment.created_at else None,
    }


@router.delete("/comments/{comment_id}", status_code=204)
def delete_comment(
    comment_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    comment = db.query(models.Comment).filter(
        models.Comment.id == comment_id,
        models.Comment.company_id == current_user.company_id,
    ).first()
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found.")
    # Author or admin may delete
    if comment.author_id != current_user.id and current_user.role != "admin":
        raise HTTPException(status_code=403, detail="You can only delete your own comments.")
    db.delete(comment)
    db.commit()


# ── scorecards ─────────────────────────────────────────────────────────────

_VALID_RECS = {"strong_yes", "yes", "no", "strong_no"}


@router.get("/applicants/{applicant_id}/scorecards")
def list_scorecards(
    applicant_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    _get_applicant(db, applicant_id, current_user.company_id)
    rows = db.query(models.Scorecard).filter(
        models.Scorecard.applicant_id == applicant_id,
        models.Scorecard.company_id == current_user.company_id,
    ).order_by(models.Scorecard.created_at.desc()).all()

    reviewer_ids = {r.reviewer_id for r in rows}
    reviewers = {
        u.id: u.email for u in db.query(models.User).filter(models.User.id.in_(reviewer_ids)).all()
    } if reviewer_ids else {}

    # Aggregate a simple recommendation tally for the UI
    tally = {k: 0 for k in _VALID_RECS}
    for r in rows:
        if r.recommendation in tally:
            tally[r.recommendation] += 1

    return {
        "scorecards": [
            {
                "id": r.id,
                "reviewer_id": r.reviewer_id,
                "reviewer_email": reviewers.get(r.reviewer_id, "unknown"),
                "recommendation": r.recommendation,
                "overall_rating": r.overall_rating,
                "stage": r.stage,
                "criteria": r.criteria or [],
                "notes": r.notes,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ],
        "summary": tally,
    }


@router.post("/applicants/{applicant_id}/scorecards", status_code=201)
def create_scorecard(
    applicant_id: int,
    payload: ScorecardCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    _get_applicant(db, applicant_id, current_user.company_id)
    if payload.recommendation not in _VALID_RECS:
        raise HTTPException(status_code=400, detail=f"recommendation must be one of {sorted(_VALID_RECS)}")
    if payload.overall_rating is not None and not (1 <= payload.overall_rating <= 4):
        raise HTTPException(status_code=400, detail="overall_rating must be 1–4.")

    sc = models.Scorecard(
        company_id=current_user.company_id,
        applicant_id=applicant_id,
        interview_id=payload.interview_id,
        reviewer_id=current_user.id,
        stage=payload.stage,
        recommendation=payload.recommendation,
        overall_rating=payload.overall_rating,
        criteria=[c.model_dump() for c in payload.criteria] if payload.criteria else None,
        notes=payload.notes,
    )
    db.add(sc)
    db.commit()
    db.refresh(sc)

    audit.record(db, current_user.company_id, "applicant.scorecard_submitted", actor=current_user,
                 entity_type="applicant", entity_id=applicant_id,
                 summary=f"Scorecard: {payload.recommendation}")

    return {"id": sc.id, "recommendation": sc.recommendation, "overall_rating": sc.overall_rating}
