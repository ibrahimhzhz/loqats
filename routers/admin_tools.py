"""
routers/admin_tools.py — Audit log, email templates, and email history.

  GET   /api/audit                     recent audit events (filterable)
  GET   /api/notifications/templates   list templates (defaults + overrides)
  PUT   /api/notifications/templates/{key}  save a company override
  GET   /api/notifications/log         recent sent/failed emails
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from datetime import datetime
from typing import Optional

from database import get_db
import models
from services.auth import get_current_user
from services import email as email_service

router = APIRouter(prefix="/api", tags=["Admin"])


# ── audit ──────────────────────────────────────────────────────────────────

@router.get("/audit")
def list_audit(
    action: Optional[str] = None,
    entity_type: Optional[str] = None,
    entity_id: Optional[int] = None,
    limit: int = Query(100, le=500),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    q = db.query(models.AuditLog).filter(
        models.AuditLog.company_id == current_user.company_id,
    )
    if action:
        q = q.filter(models.AuditLog.action == action)
    if entity_type:
        q = q.filter(models.AuditLog.entity_type == entity_type)
    if entity_id is not None:
        q = q.filter(models.AuditLog.entity_id == entity_id)

    rows = q.order_by(models.AuditLog.created_at.desc()).limit(limit).all()
    return {"events": [
        {
            "id": r.id,
            "action": r.action,
            "actor_email": r.actor_email,
            "entity_type": r.entity_type,
            "entity_id": r.entity_id,
            "summary": r.summary,
            "meta": r.meta,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]}


# ── email templates ────────────────────────────────────────────────────────

class TemplateSave(BaseModel):
    subject: str
    body: str
    enabled: bool = True


@router.get("/notifications/templates")
def list_templates(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    overrides = {
        t.key: t for t in db.query(models.EmailTemplate).filter(
            models.EmailTemplate.company_id == current_user.company_id
        ).all()
    }
    out = []
    for key, default in email_service.DEFAULT_TEMPLATES.items():
        ov = overrides.get(key)
        out.append({
            "key": key,
            "subject": ov.subject if ov else default["subject"],
            "body": ov.body if ov else default["body"],
            "enabled": bool(ov.enabled) if ov else True,
            "customized": ov is not None,
        })
    return {"templates": out}


@router.put("/notifications/templates/{key}")
def save_template(
    key: str,
    payload: TemplateSave,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Only admins can edit email templates.")
    if key not in email_service.DEFAULT_TEMPLATES:
        raise HTTPException(status_code=400, detail=f"Unknown template key '{key}'.")

    row = db.query(models.EmailTemplate).filter(
        models.EmailTemplate.company_id == current_user.company_id,
        models.EmailTemplate.key == key,
    ).first()
    if row:
        row.subject = payload.subject
        row.body = payload.body
        row.enabled = payload.enabled
        row.updated_at = datetime.utcnow()
    else:
        row = models.EmailTemplate(
            company_id=current_user.company_id,
            key=key, subject=payload.subject, body=payload.body, enabled=payload.enabled,
        )
        db.add(row)
    db.commit()
    return {"key": key, "saved": True}


@router.get("/notifications/log")
def email_log(
    limit: int = Query(100, le=500),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    rows = db.query(models.EmailLog).filter(
        models.EmailLog.company_id == current_user.company_id,
    ).order_by(models.EmailLog.created_at.desc()).limit(limit).all()
    return {"emails": [
        {
            "id": r.id, "to_email": r.to_email, "template_key": r.template_key,
            "subject": r.subject, "status": r.status, "error": r.error,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]}
