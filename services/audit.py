"""
services/audit.py — Append-only audit trail.

Every consequential action (stage change, score override, deletion, export,
offer sent, bulk action) should call record(). Rows are never updated or
deleted. This is the backbone of the "defend a hiring decision" story and of
compliance with regimes like NYC Local Law 144 and the EU AI Act.
"""

from __future__ import annotations

import logging
from typing import Optional, Any, Dict

logger = logging.getLogger(__name__)


def record(
    db,
    company_id: int,
    action: str,
    actor=None,
    entity_type: Optional[str] = None,
    entity_id: Optional[int] = None,
    summary: Optional[str] = None,
    meta: Optional[Dict[str, Any]] = None,
    commit: bool = True,
) -> None:
    """
    Append an audit row. Best-effort: an audit failure must never break the
    action it is recording, so all exceptions are swallowed and logged.

    `actor` may be a User ORM object or None (for system actions).
    """
    try:
        import models
        actor_id = getattr(actor, "id", None)
        actor_email = getattr(actor, "email", None)
        db.add(models.AuditLog(
            company_id=company_id,
            actor_id=actor_id,
            actor_email=actor_email,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            summary=summary,
            meta=meta,
        ))
        if commit:
            db.commit()
    except Exception as exc:
        logger.warning("Audit record failed for action=%s: %s", action, exc)
        try:
            db.rollback()
        except Exception:
            pass
