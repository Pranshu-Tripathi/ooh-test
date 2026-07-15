from typing import NoReturn

from fastapi import HTTPException, status

from ooh.services import ConflictError, NotFoundError, ServiceError


def raise_http_for_service_error(exc: ServiceError) -> NoReturn:
    if isinstance(exc, NotFoundError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, ConflictError):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
