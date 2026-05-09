"""Unit tests for the llm_job tool (litellm-backed)."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from trellis.tools.impls.llm import LLMTool, DEFAULT_LLM_MODEL, _DEFAULT_SYSTEM_PROMPT


def _mock_completion(content: str):
    """Build a minimal litellm-style response object."""
    msg = SimpleNamespace(content=content)
    choice = SimpleNamespace(message=msg)
    return SimpleNamespace(choices=[choice])


def _call_messages(mock_litellm) -> list[dict]:
    return mock_litellm.completion.call_args.kwargs["messages"]


@pytest.fixture
def tool():
    return LLMTool()


# ---------------------------------------------------------------------------
# Basic execution
# ---------------------------------------------------------------------------


def test_execute_returns_llm_content(tool):
    with patch("trellis.tools.impls.llm.litellm") as mock_litellm:
        mock_litellm.completion.return_value = _mock_completion("hello")
        result = tool.execute(prompt="Say hello")
    assert result == "hello"


def test_execute_user_message_contains_prompt(tool):
    with patch("trellis.tools.impls.llm.litellm") as mock_litellm:
        mock_litellm.completion.return_value = _mock_completion("ok")
        tool.execute(prompt="Say hello")

    messages = _call_messages(mock_litellm)
    user_msg = next(m for m in messages if m["role"] == "user")
    assert "Say hello" in user_msg["content"]


def test_execute_uses_default_model(tool):
    with patch("trellis.tools.impls.llm.litellm") as mock_litellm:
        mock_litellm.completion.return_value = _mock_completion("ok")
        tool.execute(prompt="Hi")

    call_kwargs = mock_litellm.completion.call_args.kwargs
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


# ---------------------------------------------------------------------------
# System message — default behaviour
# ---------------------------------------------------------------------------


def test_execute_sends_system_message_by_default(tool):
    """A system message is included in every call when no override is given."""
    with patch("trellis.tools.impls.llm.litellm") as mock_litellm:
        mock_litellm.completion.return_value = _mock_completion("ok")
        tool.execute(prompt="Summarise this.")

    messages = _call_messages(mock_litellm)
    roles = [m["role"] for m in messages]
    assert "system" in roles


def test_execute_system_message_is_first(tool):
    with patch("trellis.tools.impls.llm.litellm") as mock_litellm:
        mock_litellm.completion.return_value = _mock_completion("ok")
        tool.execute(prompt="Summarise this.")

    messages = _call_messages(mock_litellm)
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"


def test_execute_default_system_prompt_content(tool):
    """Default system prompt suppresses preamble."""
    with patch("trellis.tools.impls.llm.litellm") as mock_litellm:
        mock_litellm.completion.return_value = _mock_completion("ok")
        tool.execute(prompt="Summarise this.")

    messages = _call_messages(mock_litellm)
    system_content = messages[0]["content"]
    assert "Respond directly" in system_content or "preamble" in system_content.lower()


# ---------------------------------------------------------------------------
# System message — per-call override via `system=` input
# ---------------------------------------------------------------------------


def test_execute_system_override_replaces_default(tool):
    custom = "You are a financial analyst. Be precise."
    with patch("trellis.tools.impls.llm.litellm") as mock_litellm:
        mock_litellm.completion.return_value = _mock_completion("ok")
        tool.execute(prompt="Analyse this.", system=custom)

    messages = _call_messages(mock_litellm)
    system_msg = next(m for m in messages if m["role"] == "system")
    assert system_msg["content"] == custom


def test_execute_empty_system_suppresses_system_message(tool):
    """system='' means no system message is sent at all."""
    with patch("trellis.tools.impls.llm.litellm") as mock_litellm:
        mock_litellm.completion.return_value = _mock_completion("ok")
        tool.execute(prompt="Hi", system="")

    messages = _call_messages(mock_litellm)
    roles = [m["role"] for m in messages]
    assert "system" not in roles
    assert len(messages) == 1
    assert messages[0]["role"] == "user"


def test_execute_system_not_treated_as_context_data(tool):
    """system= must not appear as a context data section in the user message."""
    with patch("trellis.tools.impls.llm.litellm") as mock_litellm:
        mock_litellm.completion.return_value = _mock_completion("ok")
        tool.execute(prompt="Summarise.", system="You are a helpful assistant.")

    messages = _call_messages(mock_litellm)
    user_msg = next(m for m in messages if m["role"] == "user")
    assert "--- system ---" not in user_msg["content"]


# ---------------------------------------------------------------------------
# System message — env var override
# ---------------------------------------------------------------------------


def test_default_system_prompt_env_override(monkeypatch, tool):
    """TRELLIS_LLM_SYSTEM_PROMPT sets the module-level default."""
    import trellis.tools.impls.llm as llm_mod
    monkeypatch.setattr(llm_mod, "_DEFAULT_SYSTEM_PROMPT", "Custom global default.")

    with patch("trellis.tools.impls.llm.litellm") as mock_litellm:
        mock_litellm.completion.return_value = _mock_completion("ok")
        tool.execute(prompt="Hi")

    messages = _call_messages(mock_litellm)
    system_msg = next(m for m in messages if m["role"] == "system")
    assert system_msg["content"] == "Custom global default."


def test_empty_env_var_disables_system_message(monkeypatch, tool):
    """When TRELLIS_LLM_SYSTEM_PROMPT is empty, no system message is sent by default."""
    import trellis.tools.impls.llm as llm_mod
    monkeypatch.setattr(llm_mod, "_DEFAULT_SYSTEM_PROMPT", "")

    with patch("trellis.tools.impls.llm.litellm") as mock_litellm:
        mock_litellm.completion.return_value = _mock_completion("ok")
        tool.execute(prompt="Hi")

    messages = _call_messages(mock_litellm)
    assert all(m["role"] != "system" for m in messages)


# ---------------------------------------------------------------------------
# get_inputs schema
# ---------------------------------------------------------------------------


def test_get_inputs_declares_prompt_required(tool):
    inputs = tool.get_inputs()
    assert "prompt" in inputs
    assert inputs["prompt"].required is True
    assert inputs["prompt"].accepted_types == (str,)


def test_get_inputs_declares_system_optional(tool):
    inputs = tool.get_inputs()
    assert "system" in inputs
    assert inputs["system"].required is False
    assert inputs["system"].default is None


def test_get_inputs_declares_temperature_optional(tool):
    inputs = tool.get_inputs()
    assert "temperature" in inputs
    assert inputs["temperature"].required is False


def test_default_model_constant():
    assert DEFAULT_LLM_MODEL  # non-empty
