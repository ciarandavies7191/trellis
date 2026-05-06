"""
tests/unit/execution/test_params.py — Tests for pipeline params resolution.

Covers:
  - {{params.key}} template resolution via ResolutionContext
  - _resolve_and_validate_params(): merge, required check, coercion
  - _apply_params_to_goal(): goal string substitution
  - Orchestrator.run_pipeline(): end-to-end param passing
"""

from __future__ import annotations

import pytest

from trellis.exceptions import PipelineParamError
from trellis.execution.orchestrator import (
    Orchestrator,
    _apply_params_to_goal,
    _resolve_and_validate_params,
)
from trellis.execution.template import ResolutionContext, resolve
from trellis.models.pipeline import Pipeline


# ---------------------------------------------------------------------------
# Template resolution: {{params.key}}
# ---------------------------------------------------------------------------


class TestParamsTemplateResolution:

    def _ctx(self, params: dict) -> ResolutionContext:
        return ResolutionContext(pipeline_params=params)

    def test_whole_value_params_ref(self) -> None:
        ctx = self._ctx({"ticker": "AAPL"})
        assert resolve("{{params.ticker}}", ctx) == "AAPL"

    def test_embedded_params_ref(self) -> None:
        ctx = self._ctx({"ticker": "AAPL", "year": 2023})
        result = resolve("{{params.ticker}}_{{params.year}}_doc", ctx)
        assert result == "AAPL_2023_doc"

    def test_params_integer_preserved_in_whole_value(self) -> None:
        ctx = self._ctx({"year": 2023})
        result = resolve("{{params.year}}", ctx)
        assert result == 2023
        assert isinstance(result, int)

    def test_params_list_preserved_in_whole_value(self) -> None:
        ctx = self._ctx({"tickers": ["AAPL", "MSFT"]})
        result = resolve("{{params.tickers}}", ctx)
        assert result == ["AAPL", "MSFT"]

    def test_params_missing_key_raises(self) -> None:
        from trellis.exceptions import ResolutionError
        ctx = self._ctx({"ticker": "AAPL"})
        with pytest.raises(ResolutionError, match="param 'unknown' not found"):
            resolve("{{params.unknown}}", ctx)

    def test_params_no_key_name_raises(self) -> None:
        from trellis.exceptions import ResolutionError
        ctx = self._ctx({})
        with pytest.raises(ResolutionError, match="'params' requires a key name"):
            resolve("{{params}}", ctx)

    def test_params_nested_field_access(self) -> None:
        ctx = self._ctx({"config": {"mode": "fast"}})
        result = resolve("{{params.config.mode}}", ctx)
        assert result == "fast"


# ---------------------------------------------------------------------------
# _resolve_and_validate_params
# ---------------------------------------------------------------------------


class TestResolveAndValidateParams:

    def _pipeline(self, params_yaml: str) -> Pipeline:
        return Pipeline.from_yaml(f"""
pipeline:
  id: test
  goal: Test
  params:
{params_yaml}
  tasks:
    - id: step
      tool: mock
      inputs:
        key: value
""")

    def test_required_param_provided(self) -> None:
        pipeline = self._pipeline(
            "    ticker:\n      type: string\n"
        )
        result = _resolve_and_validate_params(pipeline, {"ticker": "AAPL"})
        assert result == {"ticker": "AAPL"}

    def test_required_param_missing_raises(self) -> None:
        pipeline = self._pipeline(
            "    ticker:\n      type: string\n"
        )
        with pytest.raises(PipelineParamError, match="required param 'ticker'"):
            _resolve_and_validate_params(pipeline, {})

    def test_optional_param_uses_default(self) -> None:
        pipeline = self._pipeline(
            "    year:\n      type: integer\n      default: 2024\n"
        )
        result = _resolve_and_validate_params(pipeline, {})
        assert result == {"year": 2024}

    def test_optional_param_overridden_by_call_time(self) -> None:
        pipeline = self._pipeline(
            "    year:\n      type: integer\n      default: 2024\n"
        )
        result = _resolve_and_validate_params(pipeline, {"year": 2023})
        assert result == {"year": 2023}

    def test_type_coercion_applied(self) -> None:
        pipeline = self._pipeline(
            "    year:\n      type: integer\n"
        )
        result = _resolve_and_validate_params(pipeline, {"year": "2023"})
        assert result["year"] == 2023
        assert isinstance(result["year"], int)

    def test_unknown_params_ignored_with_warning(self, caplog) -> None:
        import logging
        pipeline = self._pipeline(
            "    ticker:\n      type: string\n"
        )
        with caplog.at_level(logging.WARNING):
            result = _resolve_and_validate_params(pipeline, {"ticker": "AAPL", "extra": "ignored"})
        assert result == {"ticker": "AAPL"}
        assert "extra" in caplog.text

    def test_no_params_block_empty_result(self) -> None:
        pipeline = Pipeline.from_yaml("""
pipeline:
  id: no_params
  goal: Test
  tasks:
    - id: step
      tool: mock
      inputs:
        key: value
""")
        result = _resolve_and_validate_params(pipeline, None)
        assert result == {}


# ---------------------------------------------------------------------------
# _apply_params_to_goal
# ---------------------------------------------------------------------------


class TestApplyParamsToGoal:

    def test_substitutes_params(self) -> None:
        goal = "Fetch {{params.company}} 10-K for FY{{params.year}}"
        result = _apply_params_to_goal(goal, {"company": "Apple Inc.", "year": 2023})
        assert result == "Fetch Apple Inc. 10-K for FY2023"

    def test_no_params_refs_unchanged(self) -> None:
        goal = "Fetch 10-K filing"
        assert _apply_params_to_goal(goal, {"company": "Apple"}) == goal

    def test_unknown_param_left_as_is(self) -> None:
        goal = "Fetch {{params.unknown}}"
        result = _apply_params_to_goal(goal, {})
        assert result == "Fetch {{params.unknown}}"

    def test_non_params_refs_left_as_is(self) -> None:
        goal = "Fetch {{fetch_task.output.url}} and {{params.ticker}}"
        result = _apply_params_to_goal(goal, {"ticker": "AAPL"})
        assert result == "Fetch {{fetch_task.output.url}} and AAPL"


# ---------------------------------------------------------------------------
# Orchestrator end-to-end
# ---------------------------------------------------------------------------


class TestOrchestratorWithParams:

    def _make_pipeline(self) -> Pipeline:
        return Pipeline.from_yaml("""
pipeline:
  id: fetch_10k
  goal: "Fetch {{params.company}} 10-K for FY{{params.fiscal_year}}"
  params:
    company:
      type: string
      description: Company name
    ticker:
      type: string
    fiscal_year:
      type: integer
      default: 2024
  tasks:
    - id: result
      tool: mock
      inputs:
        company: "{{params.company}}"
        ticker: "{{params.ticker}}"
        year: "{{params.fiscal_year}}"
""")

    @pytest.mark.asyncio
    async def test_params_resolved_in_task_inputs(self) -> None:
        pipeline = self._make_pipeline()
        orch = Orchestrator()
        result = await orch.run_pipeline(
            pipeline,
            params={"company": "Apple Inc.", "ticker": "AAPL", "fiscal_year": 2023},
        )
        assert result.tasks_executed == 1

    @pytest.mark.asyncio
    async def test_missing_required_param_raises(self) -> None:
        pipeline = self._make_pipeline()
        orch = Orchestrator()
        with pytest.raises(PipelineParamError, match="required param 'company'"):
            await orch.run_pipeline(pipeline, params={"ticker": "AAPL"})

    @pytest.mark.asyncio
    async def test_default_param_used_when_not_supplied(self) -> None:
        pipeline = self._make_pipeline()
        orch = Orchestrator()
        result = await orch.run_pipeline(
            pipeline,
            params={"company": "Apple Inc.", "ticker": "AAPL"},
        )
        assert result.tasks_executed == 1

    @pytest.mark.asyncio
    async def test_goal_resolved_with_params(self) -> None:
        pipeline = self._make_pipeline()
        orch = Orchestrator()
        result = await orch.run_pipeline(
            pipeline,
            params={"company": "Apple Inc.", "ticker": "AAPL", "fiscal_year": 2023},
        )
        assert result.tasks_executed == 1
