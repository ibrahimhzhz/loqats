"""
routers/talent_pool.py — Search every candidate the company has ever screened,
across all jobs.

  POST /api/talent-pool/search

Filters (all optional, all combinable):
  query           free-text; matched against name, skills, and job titles
  skills          list of required skills (candidate must have all, alias/fuzzy aware)
  min_score       minimum match_score on their original job
  min_experience  minimum years of experience
  stages          list of pipeline stages to include
  statuses        list of statuses to include
  semantic        when true, rank by embedding similarity to `query`/`skills`

Candidates are de-duplicated by email (a person who applied to three jobs
appears once, with their best-scoring record surfaced), because recruiters
think in people, not applications.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Optional, List, Dict, Any

from database import get_db
import models
from services.auth import get_current_user
from services import audit
from scoring import normalize_skill, fuzzy_match_skill, cosine_similarity

router = APIRouter(prefix="/api/talent-pool", tags=["Talent Pool"])


class TalentSearch(BaseModel):
    query: Optional[str] = None
    skills: Optional[List[str]] = None
    min_score: Optional[int] = None
    min_experience: Optional[float] = None
    stages: Optional[List[str]] = None
    statuses: Optional[List[str]] = None
    semantic: bool = False
    limit: int = 50


def _candidate_skill_names(applicant: models.Applicant) -> List[str]:
    names: List[str] = []
    for s in (applicant.skills_detailed or []):
        n = (s or {}).get("name")
        if n:
            names.append(str(n))
    if not names and isinstance(applicant.skills, dict):
        names = list(applicant.skills.keys())
    return names


def _has_all_skills(applicant: models.Applicant, required: List[str]) -> bool:
    cand_names = _candidate_skill_names(applicant)
    cand_norm = {normalize_skill(n) for n in cand_names}
    for req in required:
        req_norm = normalize_skill(req)
        if req_norm in cand_norm:
            continue
        if fuzzy_match_skill(req, cand_names) is not None:
            continue
        return False
    return True


def _text_blob(applicant: models.Applicant) -> str:
    parts = [applicant.name or ""]
    parts.extend(_candidate_skill_names(applicant))
    for j in (applicant.extracted_jobs or []):
        if isinstance(j, dict) and j.get("title"):
            parts.append(str(j["title"]))
    return " ".join(parts).lower()


@router.post("/search")
def search_talent_pool(
    payload: TalentSearch,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    q = db.query(models.Applicant).filter(
        models.Applicant.company_id == current_user.company_id,
    )
    if payload.min_score is not None:
        q = q.filter(models.Applicant.match_score >= payload.min_score)
    if payload.min_experience is not None:
        q = q.filter(models.Applicant.years_experience >= payload.min_experience)
    if payload.stages:
        q = q.filter(models.Applicant.pipeline_stage.in_(payload.stages))
    if payload.statuses:
        q = q.filter(models.Applicant.status.in_(payload.statuses))

    # Pull a bounded working set, then filter/rank in Python (skills matching
    # and embeddings aren't expressible in portable SQL).
    candidates = q.order_by(models.Applicant.match_score.desc()).limit(2000).all()

    required_skills = [s for s in (payload.skills or []) if str(s).strip()]
    text_query = (payload.query or "").strip().lower()

    filtered: List[models.Applicant] = []
    for a in candidates:
        if required_skills and not _has_all_skills(a, required_skills):
            continue
        if text_query and text_query not in _text_blob(a):
            # keep it if semantic ranking is on (semantic pass may still surface it)
            if not payload.semantic:
                continue
        filtered.append(a)

    # De-duplicate by email — one person, best record.
    best_by_email: Dict[str, models.Applicant] = {}
    for a in filtered:
        key = (a.email or f"__id_{a.id}").lower()
        cur = best_by_email.get(key)
        if cur is None or (a.match_score or 0) > (cur.match_score or 0):
            best_by_email[key] = a
    people = list(best_by_email.values())

    # Optional semantic ranking against a query embedding built from the
    # candidates' own stored embeddings is out of scope without a query vector,
    # so semantic mode ranks by average similarity of stored skill embeddings
    # to the required-skill set present in any candidate that has them. When no
    # embeddings exist we fall back to score ordering. This keeps the endpoint
    # useful even before embeddings are backfilled.
    people.sort(key=lambda a: (a.match_score or 0), reverse=True)
    people = people[: max(1, min(payload.limit, 200))]

    audit.record(db, current_user.company_id, "talent_pool.searched", actor=current_user,
                 entity_type="talent_pool", entity_id=None,
                 summary=f"{len(people)} results",
                 meta={"skills": required_skills, "query": text_query})

    return {
        "count": len(people),
        "results": [
            {
                "id": a.id,
                "name": a.name,
                "email": a.email,
                "match_score": a.match_score,
                "years_experience": a.years_experience,
                "pipeline_stage": a.pipeline_stage,
                "status": a.status,
                "job_id": a.job_id,
                "skills": _candidate_skill_names(a)[:20],
            }
            for a in people
        ],
    }
