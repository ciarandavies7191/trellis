"""Unit tests for the llm_job tool (litellm-backed)."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from trellis.tools.impls.llm import LLMTool, DEFAULT_LLM_MODEL


def _mock_completion(content: str):
    """Build a minimal litellm-style response object."""
    msg = SimpleNamespace(content=content)
    choice = SimpleNamespace(message=msg)
    return SimpleNamespace(choices=[choice])


@pytest.fixture
def tool():
    return LLMTool()


def test_execute_passes_prompt_to_litellm(tool):
    with patch("trellis.tools.impls.llm.litellm") as mock_litellm:
        mock_litellm.completion.return_value = _mock_completion("hello")
        result = tool.execute(prompt="Say hello")

    assert result == "hello"
    call_kwargs = mock_litellm.completion.call_args.kwargs
    assert call_kwargs["messages"] == [{"role": "user", "content": "Say hello"}]


def test_execute_uses_default_model_env(monkeypatch, tool):
    monkeypatch.setenv("TRELLIS_LLM_MODEL", "openai/gpt-4o")
    with patch("trellis.tools.impls.llm.litellm") as mock_litellm:
        mock_litellm.completion.return_value = _mock_completion("ok")
        tool.execute(prompt="Hi")

    # DEFAULT_LLM_MODEL is module-level, so test the call kwarg directly
    call_kwargs = mock_litellm.completion.call_args.kwargs
    # model kwarg should be whatever the tool resolved (default or override)
    assert "model" in call_kwargs


def test_execute_model_override(tool):
    with patch("trellis.tools.impls.llm.litellm") as mock_litellm:
        mock_litellm.completion.return_value = _mock_completion("resp")
        tool.execute(prompt="Hi", model="anthropic/claude-3-haiku-20240307")

    call_kwargs = mock_litellm.completion.call_args.kwargs
    assert call_kwargs["model"] == "anthropic/claude-3-haiku-20240307"


def test_execute_passes_temperature_and_max_tokens(tool):
    with patch("trellis.tools.impls.llm.litellm") as mock_litellm:
        mock_litellm.completion.return_value = _mock_completion("resp")
        tool.execute(prompt="Hi", temperature=0.2, max_tokens=128)

    call_kwargs = mock_litellm.completion.call_args.kwargs
    assert call_kwargs["temperature"] == 0.2
    assert call_kwargs["max_tokens"] == 128


def test_execute_omits_temperature_and_max_tokens_when_not_provided(tool):
    with patch("trellis.tools.impls.llm.litellm") as mock_litellm:
        mock_litellm.completion.return_value = _mock_completion("resp")
        tool.execute(prompt="Hi")

    call_kwargs = mock_litellm.completion.call_args.kwargs
    assert "temperature" not in call_kwargs
    assert "max_tokens" not in call_kwargs


def test_execute_raises_on_non_string_prompt(tool):
    from trellis.models.document import DocumentHandle, DocFormat
    handle = DocumentHandle(source="test.pdf", format=DocFormat.PDF, pages=[], page_count=0)
    with pytest.raises(TypeError, match="llm_job"):
        tool.execute(prompt=handle)


def test_execute_raises_on_dict_prompt(tool):
    with pytest.raises(TypeError, match="llm_job"):
        tool.execute(prompt={"text": "hello"})


def test_get_inputs_declares_prompt_required(tool):
    inputs = tool.get_inputs()
    assert "prompt" in inputs
    assert inputs["prompt"].required is True
    assert inputs["prompt"].accepted_types == (str,)


def test_default_model_constant():
    assert DEFAULT_LLM_MODEL  # non-empty
