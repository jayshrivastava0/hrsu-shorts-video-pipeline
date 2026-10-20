import pytest


@pytest.fixture(autouse=True)
def _ollama_backend_in_tests(monkeypatch):
    """Claude is the default live backend; unit tests must never shell out to the real `claude` CLI."""
    monkeypatch.setenv("HRSU_LLM_BACKEND", "ollama")
