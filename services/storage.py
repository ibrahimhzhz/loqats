"""
services/storage.py — Resume file storage, off the database.

Resumes were previously stored as BLOBs in Postgres and shipped through
Redis as base64. That bloats both the database and the broker. This module
provides a small backend-agnostic interface so binaries live in object
storage (or local disk in development) and the database only holds a key.

Backends, selected by environment:
  - STORAGE_BACKEND=s3      → Amazon S3 / any S3-compatible store (needs boto3)
  - STORAGE_BACKEND=gcs     → Google Cloud Storage (needs google-cloud-storage)
  - STORAGE_BACKEND=local   → local filesystem (default; good for dev)

Common env:
  STORAGE_BACKEND       (default "local")
  LOCAL_STORAGE_DIR     (default "<tempdir>/loqats_files")   [local]
  S3_BUCKET / GCS_BUCKET
  AWS_* credentials are read by boto3 from the standard chain.

The interface is intentionally tiny: save / load / delete / signed_url.
All methods are defensive — a storage failure never raises into the request
path uncaught; callers check the return value.
"""

from __future__ import annotations

import os
import io
import uuid
import tempfile
import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

STORAGE_BACKEND = os.getenv("STORAGE_BACKEND", "local").strip().lower()
LOCAL_STORAGE_DIR = os.getenv(
    "LOCAL_STORAGE_DIR", os.path.join(tempfile.gettempdir(), "loqats_files")
)
S3_BUCKET = os.getenv("S3_BUCKET", "")
S3_ENDPOINT_URL = os.getenv("S3_ENDPOINT_URL") or None  # for S3-compatible stores
GCS_BUCKET = os.getenv("GCS_BUCKET", "")
SIGNED_URL_TTL_SECONDS = int(os.getenv("STORAGE_SIGNED_URL_TTL", "3600"))


def build_resume_key(company_id: int, applicant_hint: str = "resume") -> str:
    """Return a collision-free storage key for a resume PDF."""
    safe = "".join(c for c in (applicant_hint or "resume") if c.isalnum() or c in "-_")[:40] or "resume"
    stamp = datetime.utcnow().strftime("%Y/%m")
    return f"resumes/{company_id}/{stamp}/{safe}-{uuid.uuid4().hex}.pdf"


class _LocalBackend:
    def __init__(self, root: str):
        self.root = root
        os.makedirs(self.root, exist_ok=True)

    def _path(self, key: str) -> str:
        # Prevent traversal; keys are app-generated but be safe anyway.
        safe_key = key.replace("..", "").lstrip("/")
        full = os.path.join(self.root, safe_key)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        return full

    def save(self, key: str, data: bytes, content_type: str) -> bool:
        with open(self._path(key), "wb") as f:
            f.write(data)
        return True

    def load(self, key: str) -> Optional[bytes]:
        path = self._path(key)
        if not os.path.exists(path):
            return None
        with open(path, "rb") as f:
            return f.read()

    def delete(self, key: str) -> bool:
        path = self._path(key)
        if os.path.exists(path):
            os.remove(path)
        return True

    def signed_url(self, key: str) -> Optional[str]:
        # Local disk can't produce a public URL; callers fall back to
        # streaming bytes through the API. Return None to signal that.
        return None


class _S3Backend:
    def __init__(self, bucket: str):
        import boto3  # imported lazily; only needed when configured
        self.bucket = bucket
        self.client = boto3.client("s3", endpoint_url=S3_ENDPOINT_URL)

    def save(self, key: str, data: bytes, content_type: str) -> bool:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)
        return True

    def load(self, key: str) -> Optional[bytes]:
        try:
            obj = self.client.get_object(Bucket=self.bucket, Key=key)
            return obj["Body"].read()
        except Exception:
            return None

    def delete(self, key: str) -> bool:
        self.client.delete_object(Bucket=self.bucket, Key=key)
        return True

    def signed_url(self, key: str) -> Optional[str]:
        try:
            return self.client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket, "Key": key},
                ExpiresIn=SIGNED_URL_TTL_SECONDS,
            )
        except Exception as exc:
            logger.warning("S3 signed url failed for %s: %s", key, exc)
            return None


class _GCSBackend:
    def __init__(self, bucket: str):
        from google.cloud import storage as gcs  # lazy import
        self.client = gcs.Client()
        self.bucket = self.client.bucket(bucket)

    def save(self, key: str, data: bytes, content_type: str) -> bool:
        blob = self.bucket.blob(key)
        blob.upload_from_file(io.BytesIO(data), content_type=content_type)
        return True

    def load(self, key: str) -> Optional[bytes]:
        try:
            return self.bucket.blob(key).download_as_bytes()
        except Exception:
            return None

    def delete(self, key: str) -> bool:
        self.bucket.blob(key).delete()
        return True

    def signed_url(self, key: str) -> Optional[str]:
        try:
            from datetime import timedelta
            return self.bucket.blob(key).generate_signed_url(
                expiration=timedelta(seconds=SIGNED_URL_TTL_SECONDS)
            )
        except Exception as exc:
            logger.warning("GCS signed url failed for %s: %s", key, exc)
            return None


def _build_backend():
    try:
        if STORAGE_BACKEND == "s3" and S3_BUCKET:
            return _S3Backend(S3_BUCKET)
        if STORAGE_BACKEND == "gcs" and GCS_BUCKET:
            return _GCSBackend(GCS_BUCKET)
    except Exception as exc:
        logger.error(
            "Configured storage backend '%s' failed to initialize (%s); "
            "falling back to local disk.", STORAGE_BACKEND, exc,
        )
    return _LocalBackend(LOCAL_STORAGE_DIR)


_backend = _build_backend()


def save_bytes(key: str, data: bytes, content_type: str = "application/pdf") -> bool:
    """Persist bytes under key. Returns True on success, False on failure."""
    try:
        return bool(_backend.save(key, data, content_type))
    except Exception as exc:
        logger.error("Storage save failed for %s: %s", key, exc)
        return False


def load_bytes(key: str) -> Optional[bytes]:
    """Return stored bytes for key, or None if missing/unavailable."""
    try:
        return _backend.load(key)
    except Exception as exc:
        logger.error("Storage load failed for %s: %s", key, exc)
        return None


def delete_key(key: str) -> bool:
    try:
        return bool(_backend.delete(key))
    except Exception as exc:
        logger.error("Storage delete failed for %s: %s", key, exc)
        return False


def signed_url(key: str) -> Optional[str]:
    """A time-limited direct URL, or None if the backend can't produce one."""
    try:
        return _backend.signed_url(key)
    except Exception as exc:
        logger.error("Storage signed_url failed for %s: %s", key, exc)
        return None


def store_resume(company_id: int, pdf_bytes: bytes, applicant_hint: str = "resume") -> Optional[str]:
    """
    Store a resume PDF and return its storage key, or None on failure.
    Callers persist the key on the Applicant row and keep the DB slim.
    """
    key = build_resume_key(company_id, applicant_hint)
    if save_bytes(key, pdf_bytes, "application/pdf"):
        return key
    return None
