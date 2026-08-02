from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from ooh.api.schemas.jobs import JobDetailResponse
from ooh.db import get_database
from ooh.db.repos import JobRepo

router = APIRouter(prefix="/jobs", tags=["jobs"])
job_repo = JobRepo(get_database())


@router.get("/{job_id}", response_model=JobDetailResponse)
def get_job(job_id: UUID) -> JobDetailResponse:
    job = job_repo.get(job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="job not found")
    return JobDetailResponse.from_record(job)
