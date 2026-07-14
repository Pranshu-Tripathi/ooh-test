from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
import uvicorn

from ooh import __version__
from ooh.api.routes.jobs import router as jobs_router
from ooh.api.routes.repositories import router as repositories_router
from ooh.api.routes.tests import router as tests_router
from ooh.config import get_settings
from ooh.db import check_database, check_schema_current
from ooh.logging import configure_logging

settings = get_settings()


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    check_database()
    check_schema_current()
    yield


app = FastAPI(title="Out Of Hands Test API", version=__version__, lifespan=lifespan)
app.include_router(jobs_router)
app.include_router(repositories_router)
app.include_router(tests_router)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok", "service": "api", "version": __version__}


@app.get("/readyz")
def readyz() -> dict[str, str]:
    check_database()
    check_schema_current()
    return {"status": "ready", "service": "api"}


@app.get("/runtime")
def runtime() -> dict[str, str]:
    return {
        "env": settings.env,
        "cache_root": str(settings.cache_root),
        "model_provider": settings.model_provider,
        "ollama_base_url": settings.ollama_base_url,
        "test_generator_model": settings.test_generator_model,
        "answer_judge_model": settings.answer_judge_model,
    }


def main() -> None:
    configure_logging(settings.log_level)
    uvicorn.run("ooh.api.main:app", host=settings.api_host, port=settings.api_port)


if __name__ == "__main__":
    main()
