"""
tests/unit/execution/test_template.py — Unit tests for trellis.execution.template.

Covers:
  - resolve(): whole-value rule, interpolation rule, recursive containers,
               literals, each namespace (task_id, pipeline.inputs,
               pipeline.goal, session, item)
  - Field path traversal (output.field, nested fields)
  - resolve_inputs(): full dict resolution
  - resolve_parallel_over(): list result, string error, non-iterable error
  - ResolutionContext defaults
  - Error messages include useful context
"""

from __future__ import annotations

import pytest

from trellis.exceptions import ResolutionError
from trellis.execution.template import (
    ResolutionContext,
    resolve,
    resolve_inputs,
    resolve_parallel_over,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def ctx() -> ResolutionContext:
    return ResolutionContext(
        task_outputs={
            "fetch_financials": {
                "companies": ["Google", "Apple"],
                "revenue":   [100.0, 200.0],
                "metadata":  {"source": "sec_edgar", "year": 2025},
            },
            "summarise":  "A concise financial summary.",
            "fetch_list": ["item_a", "item_b", "item_c"],
        },
        pipeline_inputs={
            "companies": ["Google", "Apple", "Microsoft"],
            "year":      2025,
            "threshold": 0.05,
        },
        pipeline_goal="Assess credit risk for a set of companies.",
        session={
            "prior_results":  {"score": 0.8, "label": "investment_grade"},
            "market_context": "Elevated energy prices in Q3 2025.",
        },
        item=None,
    )


@pytest.fixture
def fan_ctx(ctx: ResolutionContext) -> ResolutionContext:
    ctx.item = "Google"
    return ctx


# ---------------------------------------------------------------------------
# ResolutionContext defaults
# ---------------------------------------------------------------------------


class TestResolutionContextDefaults:

    def test_all_fields_default_to_empty(self) -> None:
        c = ResolutionContext()
        assert c.task_outputs    == {}
        assert c.pipeline_inputs == {}
        assert c.pipeline_goal   == ""
        assert c.session         == {}
        assert c.item            is None

    def test_default_mutable_fields_are_independent(self) -> None:
        a = ResolutionContext()
        b = ResolutionContext()
        a.task_outputs["x"] = 1
        assert "x" not in b.task_outputs


# ---------------------------------------------------------------------------
# resolve() — literals
# ---------------------------------------------------------------------------


class TestResolveLiterals:

    @pytest.mark.parametrize("literal", [42, 3.14, True, None, 0])
    def test_non_string_literals_returned_unchanged(
        self, literal: object, ctx: ResolutionContext
    ) -> None:
        assert resolve(literal, ctx) == literal

    def test_plain_string_returned_unchanged(self, ctx: ResolutionContext) -> None:
        assert resolve("just a plain string", ctx) == "just a plain string"

    def test_empty_string_returned_unchanged(self, ctx: ResolutionContext) -> None:
        assert resolve("", ctx) == ""


# ---------------------------------------------------------------------------
# resolve() — whole-value rule (type preservation)
# ---------------------------------------------------------------------------


class TestResolveWholeValue:

    def test_whole_value_dict_type_preserved(self, ctx: ResolutionContext) -> None:
        result = resolve("{{fetch_financials.output}}", ctx)
        assert isinstance(result, dict)
        assert result["companies"] == ["Google", "Apple"]

    def test_whole_value_list_type_preserved(self, ctx: ResolutionContext) -> None:
        result = resolve("{{fetch_list.output}}", ctx)
        assert isinstance(result, list)
        assert result == ["item_a", "item_b", "item_c"]

    def test_whole_value_string_preserved(self, ctx: ResolutionContext) -> None:
        result = resolve("{{summarise.output}}", ctx)
        assert result == "A concise financial summary."

    def test_whole_value_pipeline_inputs_list_preserved(self, ctx: ResolutionContext) -> None:
        result = resolve("{{pipeline.inputs.companies}}", ctx)
        assert isinstance(result, list)
        assert result == ["Google", "Apple", "Microsoft"]

    def test_whole_value_int_preserved(self, ctx: ResolutionContext) -> None:
        result = resolve("{{pipeline.inputs.year}}", ctx)
        assert result == 2025
        assert isinstance(result, int)

    def test_whole_value_session_dict_preserved(self, ctx: ResolutionContext) -> None:
        result = resolve("{{session.prior_results}}", ctx)
        assert isinstance(result, dict)
        assert result["score"] == 0.8

    def test_whitespace_inside_braces_handled(self, ctx: ResolutionContext) -> None:
        assert resolve("{{  pipeline.inputs.year  }}", ctx) == 2025

    def test_item_binding_returned(self, fan_ctx: ResolutionContext) -> None:
        assert resolve("{{item}}", fan_ctx) == "Google"


# ---------------------------------------------------------------------------
# resolve() — interpolation rule
# ---------------------------------------------------------------------------


class TestResolveInterpolation:

    def test_embedded_template_returns_string(self, ctx: ResolutionContext) -> None:
        result = resolve("Analyse {{pipeline.inputs.year}} results", ctx)
        assert isinstance(result, str)
        assert result == "Analyse 2025 results"

    def test_multiple_embedded_templates(self, ctx: ResolutionContext) -> None:
        result = resolve(
            "Year: {{pipeline.inputs.year}}, Threshold: {{pipeline.inputs.threshold}}",
            ctx,
        )
        assert "2025" in result
        assert "0.05" in result

    def test_item_in_prompt_string(self, fan_ctx: ResolutionContext) -> None:
        result = resolve("Assess credit risk for {{item}} in {{pipeline.inputs.year}}", fan_ctx)
        assert result == "Assess credit risk for Google in 2025"

    def test_pipeline_goal_embedded(self, ctx: ResolutionContext) -> None:
        result = resolve("Goal: {{pipeline.goal}}", ctx)
        assert "Assess credit risk" in result


# ---------------------------------------------------------------------------
# resolve() — field path traversal
# ---------------------------------------------------------------------------


class TestFieldPathTraversal:

    def test_output_field_access(self, ctx: ResolutionContext) -> None:
        result = resolve("{{fetch_financials.output.metadata}}", ctx)
        assert isinstance(result, dict)
        assert result["source"] == "sec_edgar"

    def test_nested_output_field_access(self, ctx: ResolutionContext) -> None:
        result = resolve("{{fetch_financials.output.metadata.year}}", ctx)
        assert result == 2025

    def test_session_field_access(self, ctx: ResolutionContext) -> None:
        result = resolve("{{session.prior_results.score}}", ctx)
        assert result == 0.8

    def test_session_nested_field_interpolated(self, ctx: ResolutionContext) -> None:
        result = resolve("Label: {{session.prior_results.label}}", ctx)
        assert result == "Label: investment_grade"

    def test_missing_output_field_raises(self, ctx: ResolutionContext) -> None:
        with pytest.raises(ResolutionError, match="nonexistent"):
            resolve("{{fetch_financials.output.nonexistent}}", ctx)

    def test_missing_session_nested_field_raises(self, ctx: ResolutionContext) -> None:
        with pytest.raises(ResolutionError, match="missing_field"):
            resolve("{{session.prior_results.missing_field}}", ctx)


# ---------------------------------------------------------------------------
# resolve() — recursive containers
# ---------------------------------------------------------------------------


class TestResolveContainers:

    def test_list_of_templates_resolved(self, ctx: ResolutionContext) -> None:
        result = resolve(["{{pipeline.inputs.year}}", "{{summarise.output}}"], ctx)
        assert result == [2025, "A concise financial summary."]

    def test_dict_values_resolved(self, ctx: ResolutionContext) -> None:
        result = resolve(
            {"year": "{{pipeline.inputs.year}}", "summary": "{{summarise.output}}"},
            ctx,
        )
        assert result == {"year": 2025, "summary": "A concise financial summary."}

    def test_nested_dict_resolved(self, ctx: ResolutionContext) -> None:
        result = resolve({"outer": {"inner": "{{pipeline.inputs.year}}"}}, ctx)
        assert result == {"outer": {"inner": 2025}}

    def test_mixed_list(self, ctx: ResolutionContext) -> None:
        result = resolve(["literal", "{{pipeline.inputs.year}}", 42], ctx)
        assert result == ["literal", 2025, 42]

    def test_dict_keys_not_resolved(self, ctx: ResolutionContext) -> None:
        result = resolve({"{{not_a_key}}": "value"}, ctx)
        assert "{{not_a_key}}" in result

    def test_error_in_list_propagates(self, ctx: ResolutionContext) -> None:
        with pytest.raises(ResolutionError):
            resolve(["{{pipeline.inputs.year}}", "{{missing.output}}"], ctx)

    def test_error_in_dict_propagates(self, ctx: ResolutionContext) -> None:
        with pytest.raises(ResolutionError):
            resolve({"key": "{{missing.output}}"}, ctx)


# ---------------------------------------------------------------------------
# resolve() — error cases
# ---------------------------------------------------------------------------


class TestResolveErrors:

    def test_missing_task_raises(self, ctx: ResolutionContext) -> None:
        with pytest.raises(ResolutionError, match="ghost_task"):
            resolve("{{ghost_task.output}}", ctx)

    def test_missing_pipeline_input_raises(self, ctx: ResolutionContext) -> None:
        with pytest.raises(ResolutionError, match="missing_input"):
            resolve("{{pipeline.inputs.missing_input}}", ctx)

    def test_missing_session_key_raises(self, ctx: ResolutionContext) -> None:
        with pytest.raises(ResolutionError, match="missing_key"):
            resolve("{{session.missing_key}}", ctx)

    def test_item_outside_fan_out_raises(self, ctx: ResolutionContext) -> None:
        with pytest.raises(ResolutionError, match="parallel_over"):
            resolve("{{item}}", ctx)

    def test_unknown_pipeline_subkey_raises(self, ctx: ResolutionContext) -> None:
        with pytest.raises(ResolutionError, match="unknown"):
            resolve("{{pipeline.unknown_subkey}}", ctx)

    def test_bare_pipeline_raises(self, ctx: ResolutionContext) -> None:
        with pytest.raises(ResolutionError):
            resolve("{{pipeline}}", ctx)

    def test_bare_session_raises(self, ctx: ResolutionContext) -> None:
        with pytest.raises(ResolutionError):
            resolve("{{session}}", ctx)

    def test_error_message_shows_available_task_ids(self, ctx: ResolutionContext) -> None:
        with pytest.raises(ResolutionError, match="fetch_financials"):
            resolve("{{missing_task.output}}", ctx)


# ---------------------------------------------------------------------------
# resolve_inputs()
# ---------------------------------------------------------------------------


class TestResolveInputs:

    def test_resolves_all_values(self, ctx: ResolutionContext) -> None:
        inputs = {
            "companies": "{{pipeline.inputs.companies}}",
            "year":      "{{pipeline.inputs.year}}",
            "prompt":    "Analyse {{pipeline.inputs.year}} data",
        }
        result = resolve_inputs(inputs, ctx)
        assert result["companies"] == ["Google", "Apple", "Microsoft"]
        assert result["year"]      == 2025
        assert result["prompt"]    == "Analyse 2025 data"

    def test_literals_passed_through(self, ctx: ResolutionContext) -> None:
        inputs = {"source": "sec_edgar", "limit": 100}
        assert resolve_inputs(inputs, ctx) == {"source": "sec_edgar", "limit": 100}

    def test_empty_inputs_returns_empty(self, ctx: ResolutionContext) -> None:
        assert resolve_inputs({}, ctx) == {}

    def test_does_not_mutate_original(self, ctx: ResolutionContext) -> None:
        inputs = {"year": "{{pipeline.inputs.year}}"}
        resolve_inputs(inputs, ctx)
        assert inputs["year"] == "{{pipeline.inputs.year}}"

    def test_task_output_resolved(self, ctx: ResolutionContext) -> None:
        result = resolve_inputs({"summary": "{{summarise.output}}"}, ctx)
        assert result["summary"] == "A concise financial summary."


# ---------------------------------------------------------------------------
# resolve_parallel_over()
# ---------------------------------------------------------------------------


class TestResolveParallelOver:

    def test_resolves_list_from_pipeline_inputs(self, ctx: ResolutionContext) -> None:
        result = resolve_parallel_over("{{pipeline.inputs.companies}}", ctx)
        assert result == ["Google", "Apple", "Microsoft"]

    def test_resolves_list_from_task_output(self, ctx: ResolutionContext) -> None:
        result = resolve_parallel_over("{{fetch_list.output}}", ctx)
        assert result == ["item_a", "item_b", "item_c"]

    def test_returns_list_type(self, ctx: ResolutionContext) -> None:
        assert isinstance(resolve_parallel_over("{{pipeline.inputs.companies}}", ctx), list)

    def test_string_result_raises(self, ctx: ResolutionContext) -> None:
        with pytest.raises(ResolutionError, match="string"):
            resolve_parallel_over("{{summarise.output}}", ctx)

    def test_int_result_raises(self, ctx: ResolutionContext) -> None:
        with pytest.raises(ResolutionError):
            resolve_parallel_over("{{pipeline.inputs.year}}", ctx)

    def test_missing_ref_raises(self, ctx: ResolutionContext) -> None:
        with pytest.raises(ResolutionError):
            resolve_parallel_over("{{missing_task.output}}", ctx)