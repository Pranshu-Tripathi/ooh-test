from ooh.agent.providers.base import ModelProvider
from ooh.agent.providers.ollama import OllamaModelProvider
from ooh.config import Settings


def build_model_provider(settings: Settings) -> ModelProvider:
    if settings.model_provider == "ollama":
        return OllamaModelProvider(base_url=settings.ollama_base_url)
    raise ValueError(f"unsupported model provider: {settings.model_provider}")
