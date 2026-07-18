from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OOH_", env_file=".env", extra="ignore")

    env: str = "development"
    log_level: str = "INFO"
    api_host: str = "0.0.0.0"
    api_port: int = 8500
    worker_poll_interval_seconds: int = Field(default=5, ge=1)
    database_url: str = "postgresql://ooh:ooh@localhost:8501/ooh"
    cache_root: Path = Path("/ooh_cache")
    model_provider: str = "ollama"
    ollama_base_url: str = "http://host.docker.internal:11434"
    model_timeout_seconds: float = Field(default=300, gt=0)
    test_generator_model: str = "qwen3:8b"
    answer_judge_model: str = "deepseek-r1:8b"
    generation_prompt_max_bytes: int = Field(default=8_000, ge=4_000)
    tool_observation_max_bytes: int = Field(default=2_500, ge=500)


@lru_cache
def get_settings() -> Settings:
    return Settings()
