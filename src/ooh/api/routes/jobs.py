from uuid import UUID

from fastapi import APIRouter

from ooh.api.errors import raise_http_for_service_error
from ooh.api.schemas.jobs import JobDetailResponse
from ooh.services import ServiceError, build_job_service

router = APIRouter(prefix="/jobs", tags=["jobs"])
job_service = build_job_service()


@router.get("/{job_id}", response_model=JobDetailResponse)
def get_job(job_id: UUID) -> JobDetailResponse:
    try:
        job = job_service.get_job(job_id)
    except ServiceError as exc:
        raise_http_for_service_error(exc)
    return JobDetailResponse.from_record(job)
