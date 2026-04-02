"""Unit tests for llm_job tool provider selection and inputs."""

import pytest

from trellis.tools.impls.llm import LLMTool, _OpenAIProvider, _OllamaProvider


class _StubOpenAI(_OpenAIProvider):
    def __init__(self):
        # Bypass API key check
        self.api_key = "test-key"

    def generate(self, prompt: str, *, model=None, temperature=None, max_tokens=None) -> str:
        return f"openai[{model},{temperature},{max_tokens}]: {prompt[:10]}"


class _StubOllama(_OllamaProvider):
    def __init__(self):
        self.host = "http://stub"

    def generate(self, prompt: str, *, model=None, temperature=None, max_tokens=None) -> str:
        return f"ollama[{model},{temperature},{max_tokens}]: {prompt[:10]}"


@pytest.fixture
def no_env(monkeypatch):
    for k in [
        "TRELLIS_LLM_PROVIDER",
        "OPENAI_API_KEY",
        "OPENAI_MODEL",
        "OLLAMA_HOST",
        "OLLAMA_MODEL",
    ]:
        monkeypatch.delenv(k, raising=False)


def test_llm_tool_openai_override(monkeypatch, no_env):
    tool = LLMTool()

    # Force provider selection to return stub
    monkeypatch.setattr("trellis.tools.impls.llm._OpenAIProvider", lambda api_key=None: _StubOpenAI())

    out = tool.execute(
        prompt="Hello world",
        provider="openai",
        model="gpt-test",
        temperature=0.1,
        max_tokens=50,
    )
    assert out.startswith("openai[") and "Hello" in out


def test_llm_tool_ollama_override(monkeypatch, no_env):
    tool = LLMTool()

    # Force provider selection to return stub
    monkeypatch.setattr("trellis.tools.impls.llm._OllamaProvider", lambda host=None: _StubOllama())

    out = tool.execute(
        prompt="Some text to summarize",
        provider="ollama",
        model="llama-test",
        temperature=0.7,
        max_tokens=100,
    )
    assert out.startswith("ollama[") and "Some text"[:10] in out


def test_llm_tool_defaults_openai_env(monkeypatch):
    # Default provider is openai; set a fake key and model via env
    monkeypatch.setenv("OPENAI_API_KEY", "KEY")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-default")

    tool = LLMTool()
    # Stub provider to avoid network
    monkeypatch.setattr("trellis.tools.impls.llm._OpenAIProvider", lambda api_key=None: _StubOpenAI())

    out = tool.execute(prompt="Env based")
    assert out.startswith("openai[")

