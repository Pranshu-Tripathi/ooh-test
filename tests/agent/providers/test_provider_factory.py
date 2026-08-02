import pytest

from ooh.agent.providers import OllamaModelProvider, build_model_provider
from ooh.config import Settings


def test_build_model_provider_supports_ollama() -> None:
    provider = build_model_provider(
        Settings(model_provider="ollama", ollama_base_url="http://ollama.local")
    )

    assert isinstance(provider, OllamaModelProvider)


def test_build_model_provider_rejects_unknown_provider() -> None:
    with pytest.raises(ValueError, match="unsupported model provider"):
        build_model_provider(Settings(model_provider="not-real"))
