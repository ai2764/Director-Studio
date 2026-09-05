from .ollama import OllamaLLMProvider
from .provider import LLMProvider


def get_llm_provider() -> LLMProvider:
    """Return the configured Director LLM provider.

    Ollama is the only provider today; callers depend on the boundary so a remote
    provider can be introduced without leaking its transport into Director APIs.
    """
    return OllamaLLMProvider()


__all__ = ["LLMProvider", "OllamaLLMProvider", "get_llm_provider"]
