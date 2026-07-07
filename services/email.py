"""
services/email.py — Candidate & recruiter notifications.

Sends transactional email through whichever provider is configured, and is a
safe no-op (logged, never raised) when nothing is configured — so local
development and CI never break because SMTP isn't set up.

Provider selection, by environment:
  - RESEND_API_KEY set        → Resend HTTP API
  - SENDGRID_API_KEY set      → SendGrid HTTP API
  - SMTP_HOST set             → SMTP (smtplib)
  - none of the above         → log-only (records an EmailLog with status
                                "skipped"); nothing is sent.

Common env:
  EMAIL_FROM         (e.g. "Acme Careers <careers@acme.com>")
  APP_BASE_URL       (used in template links; default http://localhost:8000)

Templates: defaults live here; a company can override per-event via the
EmailTemplate table. Bodies use Python str.format with named fields, so a
template author writes {candidate_name}, {job_title}, etc.
"""

from __future__ import annotations

import os
import json
import logging
import smtplib
import urllib.request
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

EMAIL_FROM = os.getenv("EMAIL_FROM", "LoqATS <no-reply@loqats.local>")
APP_BASE_URL = os.getenv("APP_BASE_URL", "http://localhost:8000").rstrip("/")
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY", "")
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_USE_TLS = os.getenv("SMTP_USE_TLS", "true").lower() in {"1", "true", "yes"}


# ── Default templates ──────────────────────────────────────────────────────
# Each is (subject, body). Fields available depend on the event; unknown
# fields in .format() are guarded so a template referencing a missing field
# degrades gracefully rather than raising.
DEFAULT_TEMPLATES: Dict[str, Dict[str, str]] = {
    "application_received": {
        "subject": "We received your application for {job_title}",
        "body": (
            "Hi {candidate_name},\n\n"
            "Thanks for applying to the {job_title} role at {company_name}. "
            "Your application is in and our team will review it shortly.\n\n"
            "We'll be in touch about next steps.\n\n"
            "— {company_name} Hiring Team"
        ),
    },
    "stage_changed": {
        "subject": "Update on your application for {job_title}",
        "body": (
            "Hi {candidate_name},\n\n"
            "There's an update on your application for {job_title} at "
            "{company_name}: you've moved to the {new_stage} stage.\n\n"
            "We'll follow up with anything you need to do next.\n\n"
            "— {company_name} Hiring Team"
        ),
    },
    "rejected": {
        "subject": "Update on your application for {job_title}",
        "body": (
            "Hi {candidate_name},\n\n"
            "Thank you for your interest in the {job_title} role at "
            "{company_name} and for the time you put into applying.\n\n"
            "After careful review we've decided not to move forward at this "
            "time. This was a difficult decision and we genuinely wish you "
            "well in your search.\n\n"
            "— {company_name} Hiring Team"
        ),
    },
    "interview_scheduled": {
        "subject": "Interview scheduled: {job_title} at {company_name}",
        "body": (
            "Hi {candidate_name},\n\n"
            "Your {interview_title} for the {job_title} role is scheduled for "
            "{interview_when}.\n\n"
            "{interview_location_line}\n\n"
            "If you need to reschedule, just reply to this email.\n\n"
            "— {company_name} Hiring Team"
        ),
    },
    "offer_sent": {
        "subject": "Your offer for {job_title} at {company_name}",
        "body": (
            "Hi {candidate_name},\n\n"
            "We're delighted to offer you the {job_title} role at "
            "{company_name}.\n\n"
            "{offer_body}\n\n"
            "Please review and let us know if you have any questions.\n\n"
            "— {company_name} Hiring Team"
        ),
    },
}


class _SafeDict(dict):
    """Leaves unknown {placeholders} intact instead of raising KeyError."""
    def __missing__(self, key):
        return "{" + key + "}"


def render(template_body: str, fields: Dict[str, Any]) -> str:
    try:
        return template_body.format_map(_SafeDict(**{k: ("" if v is None else v) for k, v in fields.items()}))
    except Exception:
        return template_body


def get_template(db, company_id: Optional[int], key: str) -> Dict[str, str]:
    """
    Resolve a template: company override if present and enabled, else default.
    Returns {"subject", "body", "enabled"}.
    """
    default = DEFAULT_TEMPLATES.get(key, {"subject": "{job_title}", "body": ""})
    result = {"subject": default["subject"], "body": default["body"], "enabled": True}
    if db is None or company_id is None:
        return result
    try:
        import models
        row = (
            db.query(models.EmailTemplate)
            .filter(
                models.EmailTemplate.company_id == company_id,
                models.EmailTemplate.key == key,
            )
            .first()
        )
        if row:
            result = {"subject": row.subject, "body": row.body, "enabled": bool(row.enabled)}
    except Exception as exc:
        logger.warning("Template lookup failed for %s/%s: %s", company_id, key, exc)
    return result


def _provider_configured() -> bool:
    return bool(RESEND_API_KEY or SENDGRID_API_KEY or SMTP_HOST)


def _send_via_resend(to_email: str, subject: str, body: str) -> None:
    payload = json.dumps({
        "from": EMAIL_FROM, "to": [to_email], "subject": subject, "text": body,
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.resend.com/emails", data=payload, method="POST",
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json",
        },
    )
    urllib.request.urlopen(req, timeout=15).read()


def _send_via_sendgrid(to_email: str, subject: str, body: str) -> None:
    # Parse "Name <addr>" into SendGrid's shape.
    from_addr = EMAIL_FROM
    if "<" in EMAIL_FROM and ">" in EMAIL_FROM:
        from_addr = EMAIL_FROM.split("<", 1)[1].split(">", 1)[0].strip()
    payload = json.dumps({
        "personalizations": [{"to": [{"email": to_email}]}],
        "from": {"email": from_addr},
        "subject": subject,
        "content": [{"type": "text/plain", "value": body}],
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.sendgrid.com/v3/mail/send", data=payload, method="POST",
        headers={
            "Authorization": f"Bearer {SENDGRID_API_KEY}",
            "Content-Type": "application/json",
        },
    )
    urllib.request.urlopen(req, timeout=15).read()


def _send_via_smtp(to_email: str, subject: str, body: str) -> None:
    msg = MIMEMultipart()
    msg["From"] = EMAIL_FROM
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as server:
        if SMTP_USE_TLS:
            server.starttls()
        if SMTP_USER:
            server.login(SMTP_USER, SMTP_PASSWORD)
        server.sendmail(EMAIL_FROM, [to_email], msg.as_string())


def _log_email(db, company_id, applicant_id, to_email, key, subject, status, error=None):
    if db is None:
        return
    try:
        import models
        db.add(models.EmailLog(
            company_id=company_id, applicant_id=applicant_id, to_email=to_email,
            template_key=key, subject=subject, status=status, error=error,
        ))
        db.commit()
    except Exception as exc:
        logger.warning("EmailLog write failed: %s", exc)
        try:
            db.rollback()
        except Exception:
            pass


def send_templated_email(
    db,
    company_id: Optional[int],
    to_email: str,
    template_key: str,
    fields: Dict[str, Any],
    applicant_id: Optional[int] = None,
) -> bool:
    """
    Render and send a templated email. Returns True if actually sent.
    Never raises into the caller — failures are logged to EmailLog.
    """
    if not to_email or "@" not in to_email:
        return False

    tpl = get_template(db, company_id, template_key)
    if not tpl.get("enabled", True):
        _log_email(db, company_id, applicant_id, to_email, template_key,
                   tpl["subject"], "skipped", "template disabled")
        return False

    fields = {**fields}
    fields.setdefault("app_url", APP_BASE_URL)
    subject = render(tpl["subject"], fields)
    body = render(tpl["body"], fields)

    if not _provider_configured():
        logger.info("[email:skipped-no-provider] to=%s subject=%s", to_email, subject)
        _log_email(db, company_id, applicant_id, to_email, template_key,
                   subject, "skipped", "no email provider configured")
        return False

    try:
        if RESEND_API_KEY:
            _send_via_resend(to_email, subject, body)
        elif SENDGRID_API_KEY:
            _send_via_sendgrid(to_email, subject, body)
        else:
            _send_via_smtp(to_email, subject, body)
        _log_email(db, company_id, applicant_id, to_email, template_key, subject, "sent")
        return True
    except Exception as exc:
        logger.error("Email send failed to %s: %s", to_email, exc)
        _log_email(db, company_id, applicant_id, to_email, template_key,
                   subject, "failed", str(exc))
        return False
