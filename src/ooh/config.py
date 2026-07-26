from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from ooh.generation_config import (
    DEFAULT_GENERATION_QUESTIONS_PER_CATEGORY,
    MAX_DEFAULT_GENERATION_QUESTIONS_PER_CATEGORY,
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OOH_", env_file=".env", extra="ignore")

    env: str = "development"
    log_level: str = "INFO"
    log_format: str = "json"
    api_host: str = "0.0.0.0"
    api_port: int = 8500
    worker_poll_interval_seconds: float = Field(default=5, ge=0.1)
    worker_lease_seconds: float = Field(default=3_600, ge=30)
    worker_job_types: str | None = None
    scheduler_poll_interval_seconds: float = Field(default=5, ge=0.1)
    repository_poll_interval_seconds: float = Field(default=30, ge=1)
    repository_poll_timeout_seconds: float = Field(default=30, ge=1)
    scheduler_batch_size: int = Field(default=100, ge=1, le=1_000)
    database_url: str = "postgresql://ooh:ooh@localhost:8501/ooh"
    cache_root: Path = Path("/ooh_cache")
    model_provider: str = "ollama"
    ollama_base_url: str = "http://host.docker.internal:11434"
    model_timeout_seconds: float = Field(default=300, gt=0)
    test_generator_model: str = "qwen3:8b"
    answer_judge_model: str = "deepseek-r1:8b"
    generation_questions_per_category: int = Field(
        default=DEFAULT_GENERATION_QUESTIONS_PER_CATEGORY,
        ge=1,
        le=MAX_DEFAULT_GENERATION_QUESTIONS_PER_CATEGORY,
    )
    generation_prompt_max_bytes: int = Field(default=8_000, ge=4_000)
    tool_observation_max_bytes: int = Field(default=2_500, ge=500)
    agent_loop_timeout_seconds: float = Field(default=600, gt=0)


@lru_cache
def get_settings() -> Settings:
    return Settings()
