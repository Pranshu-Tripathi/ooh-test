from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

from ooh import __version__
from ooh.api.routes.jobs import router as jobs_router
from ooh.api.routes.repositories import router as repositories_router
from ooh.api.routes.tests import router as tests_router
from ooh.api.routes.traces import router as traces_router
from ooh.config import get_settings
from ooh.db import check_database, check_schema_current
from ooh.generation_config import (
    MAX_GENERATION_QUESTIONS_PER_CATEGORY,
    MAX_GENERATION_QUESTIONS_PER_JOB,
)
from ooh.health import check_cache_writable
from ooh.logging import configure_logging

settings = get_settings()
FRONTEND_DIST_PATH = Path(__file__).resolve().parents[3] / "frontend" / "dist"
FRONTEND_ASSETS_PATH = FRONTEND_DIST_PATH / "assets"
FRONTEND_INDEX_PATH = FRONTEND_DIST_PATH / "index.html"


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    check_database()
    check_schema_current()
    check_cache_writable(settings.cache_root)
    yield


app = FastAPI(title="Out Of Hands Test API", version=__version__, lifespan=lifespan)
app.include_router(jobs_router)
app.include_router(repositories_router)
app.include_router(tests_router)
app.include_router(traces_router)
if FRONTEND_ASSETS_PATH.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_ASSETS_PATH), name="frontend-assets")


@app.get("/", include_in_schema=False)
def ui() -> FileResponse:
    if not FRONTEND_INDEX_PATH.exists():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="frontend build is not available",
        )
    return FileResponse(FRONTEND_INDEX_PATH)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok", "service": "api", "version": __version__}


@app.get("/readyz")
def readyz() -> dict[str, str]:
    check_database()
    check_schema_current()
    check_cache_writable(settings.cache_root)
    return {"status": "ready", "service": "api"}


@app.get("/runtime")
def runtime() -> dict[str, str | int]:
    return {
        "env": settings.env,
        "cache_root": str(settings.cache_root),
        "model_provider": settings.model_provider,
        "ollama_base_url": settings.ollama_base_url,
        "test_generator_model": settings.test_generator_model,
        "answer_judge_model": settings.answer_judge_model,
        "generation_questions_per_category": (
            settings.generation_questions_per_category
        ),
        "generation_max_questions_per_category": (
            MAX_GENERATION_QUESTIONS_PER_CATEGORY
        ),
        "generation_max_questions_per_job": MAX_GENERATION_QUESTIONS_PER_JOB,
    }


def main() -> None:
    configure_logging(settings.log_level, log_format=settings.log_format)
    uvicorn.run("ooh.api.main:app", host=settings.api_host, port=settings.api_port)


if __name__ == "__main__":
    main()
