from fastapi import FastAPI
import uvicorn

from ooh import __version__
from ooh.config import get_settings
from ooh.db import check_database
from ooh.logging import configure_logging


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Out Of Hands Test API", version=__version__)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok", "service": "api", "version": __version__}

    @app.get("/readyz")
    def readyz() -> dict[str, str]:
        check_database()
        return {"status": "ready", "service": "api"}

    @app.get("/runtime")
    def runtime() -> dict[str, str]:
        return {
            "env": settings.env,
            "cache_root": str(settings.cache_root),
            "ollama_base_url": settings.ollama_base_url,
            "test_generator_model": settings.test_generator_model,
            "answer_judge_model": settings.answer_judge_model,
        }

    return app


app = create_app()


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    uvicorn.run("ooh.api.main:app", host=settings.api_host, port=settings.api_port)


if __name__ == "__main__":
    main()
