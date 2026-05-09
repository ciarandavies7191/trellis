"""
Unit tests for trellis.compiler.

All LLM calls are mocked — no network access required.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

from trellis.compiler.catalog import _TEST_TOOLS, build_tool_catalog
from trellis.compiler.compiler import PipelineCompiler, _parse_and_validate, _strip_fences
from trellis.compiler.exceptions import CompilerError
from trellis.compiler.prompts import build_repair_prompt, build_system_prompt
from trellis.compiler.result import CompilerResult
from trellis.models.pipeline import Pipeline
from trellis.models.plan import Plan
from trellis.tools.registry import build_default_registry


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

MINIMAL_PIPELINE_YAML = """\
pipeline:
  id: test_pipeline
  goal: "Test pipeline"
  tasks:
    - id: greet
      tool: llm_job
      inputs:
        prompt: "Say hello."
"""

MINIMAL_PLAN_YAML = """\
plan:
  id: test_plan
  goal: "Test plan"
  sub_pipelines:
    - id: step_one
      goal: "Do step one"
      reads: []
      stores: [result]
"""

FENCED_PIPELINE_YAML = f"```yaml\n{MINIMAL_PIPELINE_YAML}```"


def _make_llm_response(content: str) -> Any:
    """Build a minimal litellm-shaped response object."""
    choice = SimpleNamespace(message=SimpleNamespace(content=content))
    return SimpleNamespace(choices=[choice])


@pytest.fixture
def registry():
    return build_default_registry()


@pytest.fixture
def compiler(registry):
    return PipelineCompiler(registry=registry, model="openai/gpt-4o-mini")


# ---------------------------------------------------------------------------
# _strip_fences
# ---------------------------------------------------------------------------


class TestStripFences:
    def test_no_fences_unchanged(self):
        text = "pipeline:\n  id: x"
        assert _strip_fences(text) == text

    def test_strips_yaml_fence(self):
        fenced = "```yaml\npipeline:\n  id: x\n```"
        assert _strip_fences(fenced) == "pipeline:\n  id: x"

    def test_strips_plain_fence(self):
        fenced = "```\npipeline:\n  id: x\n```"
        assert _strip_fences(fenced) == "pipeline:\n  id: x"

    def test_strips_surrounding_whitespace(self):
        text = "  \npipeline:\n  id: x\n  "
        assert _strip_fences(text) == "pipeline:\n  id: x"


# ---------------------------------------------------------------------------
# _parse_and_validate
# ---------------------------------------------------------------------------


class TestParseAndValidate:
    def test_valid_pipeline(self):
        pipeline, plan, clean = _parse_and_validate(MINIMAL_PIPELINE_YAML)
        assert isinstance(pipeline, Pipeline)
        assert plan is None
        assert "pipeline:" in clean

    def test_valid_plan(self):
        pipeline, plan, clean = _parse_and_validate(MINIMAL_PLAN_YAML)
        assert pipeline is None
        assert isinstance(plan, Plan)

    def test_strips_fences_before_parsing(self):
        pipeline, plan, _ = _parse_and_validate(FENCED_PIPELINE_YAML)
        assert isinstance(pipeline, Pipeline)

    def test_raises_on_wrong_root_key(self):
        bad = "workflow:\n  id: x\n"
        with pytest.raises(ValueError, match="pipeline.*plan"):
            _parse_and_validate(bad)

    def test_raises_on_non_mapping(self):
        with pytest.raises(ValueError, match="mapping"):
            _parse_and_validate("- item1\n- item2\n")

    def test_raises_on_unknown_tool(self):
        bad_yaml = """\
pipeline:
  id: test
  goal: Test
  tasks:
    - id: step
      tool: nonexistent_tool
      inputs:
        prompt: hi
"""
        with pytest.raises(Exception):
            _parse_and_validate(bad_yaml)

    def test_raises_on_cycle(self):
        cycle_yaml = """\
pipeline:
  id: cycle_test
  goal: Cycle
  tasks:
    - id: task_a
      tool: llm_job
      inputs:
        prompt: "{{task_b.output}}"
    - id: task_b
      tool: llm_job
      inputs:
        prompt: "{{task_a.output}}"
"""
        with pytest.raises(Exception, match="[Cc]ycle"):
            _parse_and_validate(cycle_yaml)

    def test_raises_on_missing_params_ref(self):
        bad = """\
pipeline:
  id: test
  goal: Test
  tasks:
    - id: step
      tool: llm_job
      inputs:
        prompt: "{{params.missing_key}}"
"""
        with pytest.raises(Exception):
            _parse_and_validate(bad)

    def test_raises_on_duplicate_task_ids(self):
        bad = """\
pipeline:
  id: test
  goal: Test
  tasks:
    - id: step
      tool: llm_job
      inputs:
        prompt: hello
    - id: step
      tool: llm_job
      inputs:
        prompt: hello
"""
        with pytest.raises(Exception, match="[Dd]uplicate"):
            _parse_and_validate(bad)


# ---------------------------------------------------------------------------
# CompilerResult
# ---------------------------------------------------------------------------


class TestCompilerResult:
    def test_is_pipeline_true(self):
        pipeline = Pipeline.from_yaml(MINIMAL_PIPELINE_YAML)
        result = CompilerResult(yaml_text=MINIMAL_PIPELINE_YAML, pipeline=pipeline)
        assert result.is_pipeline is True
        assert result.is_plan is False

    def test_is_plan_true(self):
        plan = Plan.from_yaml(MINIMAL_PLAN_YAML)
        result = CompilerResult(yaml_text=MINIMAL_PLAN_YAML, plan=plan)
        assert result.is_pipeline is False
        assert result.is_plan is True

    def test_artifact_returns_pipeline(self):
        pipeline = Pipeline.from_yaml(MINIMAL_PIPELINE_YAML)
        result = CompilerResult(yaml_text=MINIMAL_PIPELINE_YAML, pipeline=pipeline)
        assert result.artifact is pipeline

    def test_artifact_returns_plan(self):
        plan = Plan.from_yaml(MINIMAL_PLAN_YAML)
        result = CompilerResult(yaml_text=MINIMAL_PLAN_YAML, plan=plan)
        assert result.artifact is plan

    def test_artifact_raises_when_empty(self):
        result = CompilerResult(yaml_text="")
        with pytest.raises(RuntimeError):
            _ = result.artifact

    def test_attempts_default(self):
        pipeline = Pipeline.from_yaml(MINIMAL_PIPELINE_YAML)
        result = CompilerResult(yaml_text=MINIMAL_PIPELINE_YAML, pipeline=pipeline)
        assert result.attempts == 1

    def test_repair_history_default_empty(self):
        pipeline = Pipeline.from_yaml(MINIMAL_PIPELINE_YAML)
        result = CompilerResult(yaml_text=MINIMAL_PIPELINE_YAML, pipeline=pipeline)
        assert result.repair_history == []


# ---------------------------------------------------------------------------
# ToolCatalog
# ---------------------------------------------------------------------------


class TestToolCatalog:
    def test_excludes_test_tools(self, registry):
        catalog = build_tool_catalog(registry)
        for test_tool in _TEST_TOOLS:
            # Each test tool name should not appear as a heading line
            assert f"\n{test_tool} —" not in catalog

    def test_includes_production_tools(self, registry):
        catalog = build_tool_catalog(registry)
        for tool_name in ("llm_job", "ingest_document", "fetch_data", "store", "export"):
            assert tool_name in catalog

    def test_catalog_shows_required_inputs(self, registry):
        catalog = build_tool_catalog(registry)
        # llm_job has a required 'prompt' input
        assert "prompt" in catalog

    def test_catalog_shows_optional_inputs(self, registry):
        catalog = build_tool_catalog(registry)
        # llm_job has optional temperature
        assert "temperature" in catalog

    def test_catalog_is_string(self, registry):
        catalog = build_tool_catalog(registry)
        assert isinstance(catalog, str)
        assert len(catalog) > 0


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------


class TestBuildSystemPrompt:
    def test_contains_tool_catalog(self, registry):
        catalog = build_tool_catalog(registry)
        prompt = build_system_prompt(catalog)
        assert catalog in prompt

    def test_contains_dsl_rules(self, registry):
        catalog = build_tool_catalog(registry)
        prompt = build_system_prompt(catalog)
        assert "pipeline:" in prompt
        assert "parallel_over" in prompt
        assert "{{" in prompt

    def test_contains_output_instruction(self, registry):
        catalog = build_tool_catalog(registry)
        prompt = build_system_prompt(catalog)
        assert "YAML" in prompt

    def test_repair_prompt_contains_error(self):
        repair = build_repair_prompt("bad: yaml", "Unknown tool 'oops'")
        assert "Unknown tool 'oops'" in repair
        assert "bad: yaml" in repair


# ---------------------------------------------------------------------------
# PipelineCompiler — success paths
# ---------------------------------------------------------------------------


class TestPipelineCompilerSuccess:
    @pytest.mark.asyncio
    async def test_compile_pipeline_first_try(self, compiler):
        mock_response = _make_llm_response(MINIMAL_PIPELINE_YAML)
        with patch("trellis.compiler.compiler.litellm.acompletion", new=AsyncMock(return_value=mock_response)):
            result = await compiler.compile("Say hello using an LLM.")

        assert result.is_pipeline
        assert result.pipeline.id == "test_pipeline"
        assert result.attempts == 1
        assert result.repair_history == []

    @pytest.mark.asyncio
    async def test_compile_plan_first_try(self, compiler):
        mock_response = _make_llm_response(MINIMAL_PLAN_YAML)
        with patch("trellis.compiler.compiler.litellm.acompletion", new=AsyncMock(return_value=mock_response)):
            result = await compiler.compile("Create a two-step plan.")

        assert result.is_plan
        assert result.plan.id == "test_plan"
        assert result.attempts == 1

    @pytest.mark.asyncio
    async def test_compile_strips_markdown_fences(self, compiler):
        mock_response = _make_llm_response(FENCED_PIPELINE_YAML)
        with patch("trellis.compiler.compiler.litellm.acompletion", new=AsyncMock(return_value=mock_response)):
            result = await compiler.compile("Say hello.")

        assert result.is_pipeline
        assert "```" not in result.yaml_text

    @pytest.mark.asyncio
    async def test_compile_yaml_text_is_clean(self, compiler):
        mock_response = _make_llm_response(MINIMAL_PIPELINE_YAML)
        with patch("trellis.compiler.compiler.litellm.acompletion", new=AsyncMock(return_value=mock_response)):
            result = await compiler.compile("Say hello.")

        # yaml_text must be parseable back to the same pipeline
        reparsed = Pipeline.from_yaml(result.yaml_text)
        assert reparsed.id == result.pipeline.id

    @pytest.mark.asyncio
    async def test_compile_model_override(self, compiler):
        mock_response = _make_llm_response(MINIMAL_PIPELINE_YAML)
        mock_acomplete = AsyncMock(return_value=mock_response)
        with patch("trellis.compiler.compiler.litellm.acompletion", new=mock_acomplete):
            await compiler.compile("Hello.", model="anthropic/claude-haiku-4-5-20251001")

        call_kwargs = mock_acomplete.call_args
        assert call_kwargs.kwargs["model"] == "anthropic/claude-haiku-4-5-20251001"


# ---------------------------------------------------------------------------
# PipelineCompiler — repair loop
# ---------------------------------------------------------------------------


class TestPipelineCompilerRepair:
    @pytest.mark.asyncio
    async def test_repair_on_first_invalid(self, compiler):
        """First call returns bad YAML, second returns valid — attempts == 2."""
        bad_response = _make_llm_response("not: valid: yaml: at: all")
        good_response = _make_llm_response(MINIMAL_PIPELINE_YAML)

        mock_acomplete = AsyncMock(side_effect=[bad_response, good_response])
        with patch("trellis.compiler.compiler.litellm.acompletion", new=mock_acomplete):
            result = await compiler.compile("Say hello.", max_repair_attempts=2)

        assert result.is_pipeline
        assert result.attempts == 2
        assert len(result.repair_history) == 1
        _broken_yaml, error = result.repair_history[0]
        assert error  # non-empty error message

    @pytest.mark.asyncio
    async def test_repair_history_records_all_failures(self, compiler):
        """Two bad responses before a good one → repair_history has 2 entries."""
        bad1 = _make_llm_response("bad: yaml: one")
        bad2 = _make_llm_response("bad: yaml: two")
        good = _make_llm_response(MINIMAL_PIPELINE_YAML)

        mock_acomplete = AsyncMock(side_effect=[bad1, bad2, good])
        with patch("trellis.compiler.compiler.litellm.acompletion", new=mock_acomplete):
            result = await compiler.compile("Hello.", max_repair_attempts=3)

        assert result.attempts == 3
        assert len(result.repair_history) == 2

    @pytest.mark.asyncio
    async def test_repair_messages_include_error_context(self, compiler):
        """The repair message sent to the LLM must contain the prior error."""
        bad = _make_llm_response("bad: yaml")
        good = _make_llm_response(MINIMAL_PIPELINE_YAML)

        mock_acomplete = AsyncMock(side_effect=[bad, good])
        with patch("trellis.compiler.compiler.litellm.acompletion", new=mock_acomplete):
            await compiler.compile("Hello.", max_repair_attempts=2)

        # Second call should have 4 messages: system + user + assistant(bad) + user(repair)
        second_call_messages = mock_acomplete.call_args_list[1].kwargs["messages"]
        assert len(second_call_messages) == 4
        repair_user_msg = second_call_messages[3]["content"]
        assert "bad: yaml" in repair_user_msg  # broken YAML is quoted back

    @pytest.mark.asyncio
    async def test_zero_repair_attempts_fails_immediately(self, compiler):
        """max_repair_attempts=0 means no repair — invalid YAML → CompilerError."""
        bad = _make_llm_response("bad: yaml")
        with patch("trellis.compiler.compiler.litellm.acompletion", new=AsyncMock(return_value=bad)):
            with pytest.raises(CompilerError) as exc_info:
                await compiler.compile("Hello.", max_repair_attempts=0)

        assert exc_info.value.attempts == 1


# ---------------------------------------------------------------------------
# PipelineCompiler — failure paths
# ---------------------------------------------------------------------------


class TestPipelineCompilerFailure:
    @pytest.mark.asyncio
    async def test_raises_compiler_error_after_exhaustion(self, compiler):
        """All LLM calls return invalid YAML → CompilerError raised."""
        bad = _make_llm_response("totally: invalid: output")

        with patch("trellis.compiler.compiler.litellm.acompletion", new=AsyncMock(return_value=bad)):
            with pytest.raises(CompilerError) as exc_info:
                await compiler.compile("Hello.", max_repair_attempts=2)

        err = exc_info.value
        assert err.attempts == 3  # 1 initial + 2 repairs
        assert err.last_yaml == "totally: invalid: output"
        assert err.last_error  # non-empty

    @pytest.mark.asyncio
    async def test_compiler_error_message_contains_attempt_count(self, compiler):
        bad = _make_llm_response("bad: yaml")
        with patch("trellis.compiler.compiler.litellm.acompletion", new=AsyncMock(return_value=bad)):
            with pytest.raises(CompilerError, match="attempt"):
                await compiler.compile("Hello.", max_repair_attempts=1)

    @pytest.mark.asyncio
    async def test_llm_exception_propagates(self, compiler):
        """If the LLM API call itself raises, it should propagate (not wrapped)."""
        with patch(
            "trellis.compiler.compiler.litellm.acompletion",
            new=AsyncMock(side_effect=RuntimeError("API timeout")),
        ):
            with pytest.raises(RuntimeError, match="API timeout"):
                await compiler.compile("Hello.")


# ---------------------------------------------------------------------------
# PipelineCompiler — system prompt caching
# ---------------------------------------------------------------------------


class TestPipelineCompilerSystemPrompt:
    def test_system_prompt_built_lazily(self, registry):
        compiler = PipelineCompiler(registry=registry)
        assert compiler._system_prompt is None
        prompt = compiler._get_system_prompt()
        assert compiler._system_prompt is not None
        assert prompt == compiler._system_prompt

    def test_system_prompt_cached(self, registry):
        compiler = PipelineCompiler(registry=registry)
        p1 = compiler._get_system_prompt()
        p2 = compiler._get_system_prompt()
        assert p1 is p2  # same object, not rebuilt

    def test_system_prompt_contains_tool_names(self, compiler):
        prompt = compiler._get_system_prompt()
        assert "llm_job" in prompt
        assert "ingest_document" in prompt
        assert "fetch_data" in prompt
