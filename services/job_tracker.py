"""
Job tracking system for asynchronous bulk resume processing.
Stores job status and results in memory (can be upgraded to Redis/DB later).
"""
import uuid
from datetime import datetime
from typing import Dict, Any, Optional
from enum import Enum


class JobStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class JobTracker:
    """In-memory job tracking system with tenant isolation."""
    
    def __init__(self):
        self.jobs: Dict[str, Dict[str, Any]] = {}
    
    def create_job(self, total_resumes: int, company_id: int) -> str:
        """Create a new job and return its ID.
        
        Args:
            total_resumes: Number of resumes to process
            company_id: Tenant identifier for multi-tenant isolation
        """
        job_id = str(uuid.uuid4())
        self.jobs[job_id] = {
            "id": job_id,
            "company_id": company_id,  # ← TENANT ISOLATION
            "status": JobStatus.PENDING,
            "total_resumes": total_resumes,
            "processed": 0,
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
            "results": None,
            "error": None
        }
        return job_id
    
    def update_status(self, job_id: str, status: JobStatus, error: Optional[str] = None):
        """Update job status."""
        if job_id in self.jobs:
            self.jobs[job_id]["status"] = status
            self.jobs[job_id]["updated_at"] = datetime.now().isoformat()
            if error:
                self.jobs[job_id]["error"] = error
    
    def update_progress(self, job_id: str, processed: int):
        """Update the number of processed resumes."""
        if job_id in self.jobs:
            self.jobs[job_id]["processed"] = processed
            self.jobs[job_id]["updated_at"] = datetime.now().isoformat()
    
    def set_results(self, job_id: str, results: Dict[str, Any]):
        """Store the final results."""
        if job_id in self.jobs:
            self.jobs[job_id]["results"] = results
            self.jobs[job_id]["status"] = JobStatus.COMPLETED
            self.jobs[job_id]["updated_at"] = datetime.now().isoformat()
    
    def get_job(self, job_id: str, company_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """Retrieve job information with optional tenant filtering.
        
        Args:
            job_id: The job identifier
            company_id: If provided, only return job if it matches this company_id
        
        Returns:
            Job data if found and authorized, None otherwise
        """
        job = self.jobs.get(job_id)
        
        # If company_id filter is provided, verify ownership
        if job and company_id is not None:
            if job.get("company_id") != company_id:
                return None  # ← TENANT ISOLATION: Deny access to other companies' jobs
        
        return job
    
    def cleanup_old_jobs(self, max_age_hours: int = 24):
        """Remove jobs older than max_age_hours (optional maintenance)."""
        from datetime import timedelta
        now = datetime.now()
        to_remove = []
        
        for job_id, job in self.jobs.items():
            created = datetime.fromisoformat(job["created_at"])
            if (now - created) > timedelta(hours=max_age_hours):
                to_remove.append(job_id)
        
        for job_id in to_remove:
            del self.jobs[job_id]
        
        return len(to_remove)
    
    def get_company_jobs(self, company_id: int) -> list[Dict[str, Any]]:
        """Get all active jobs for a specific company.
        
        Args:
            company_id: The tenant identifier
            
        Returns:
            List of jobs belonging to the specified company
        """
        return [
            job for job_id, job in self.jobs.items()
            if job.get("company_id") == company_id
        ]


# Global job tracker instance
job_tracker = JobTracker()
