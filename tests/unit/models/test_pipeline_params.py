"""
tests/unit/models/test_pipeline_params.py — Tests for Pipeline.params (DSL v1.5).

Covers:
  - PipelineParam model: field defaults, type validation, coercion, required property
  - Pipeline.params field: parsing, None coercion, invalid refs caught at parse time
  - params_refs_exist model validator: goal and task inputs scanning
  - upstream_task_ids(): params refs excluded from task dependencies
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from trellis.models.pipeline import Pipeline, PipelineParam, _MISSING, VALID_PARAM_TYPES


# ---------------------------------------------------------------------------
# PipelineParam model
# ---------------------------------------------------------------------------


class TestPipelineParam:

    def test_defaults(self) -> None:
        p = PipelineParam()
        assert p.type == "string"
        assert p.description == ""
        assert p.default is _MISSING
        assert p.required is True

    def test_with_default_is_not_required(self) -> None:
        p = PipelineParam(type="integer", default=2024)
        assert p.required is False
        assert p.default == 2024

    def test_default_none_is_not_required(self) -> None:
        p = PipelineParam(type="string", default=None)
        assert p.required is False
        assert p.default is None

    def test_invalid_type_raises(self) -> None:
        with pytest.raises(ValidationError, match="Invalid param type"):
            PipelineParam(type="uuid")

    def test_all_valid_types_accepted(self) -> None:
        for t in VALID_PARAM_TYPES:
            p = PipelineParam(type=t)
            assert p.type == t

    # Coercion ---

    def test_coerce_string(self) -> None:
        p = PipelineParam(type="string")
        assert p.coerce(42, "x") == "42"

    def test_coerce_integer(self) -> None:
        p = PipelineParam(type="integer")
        assert p.coerce("2023", "year") == 2023

    def test_coerce_integer_already_int(self) -> None:
        p = PipelineParam(type="integer")
        assert p.coerce(5, "n") == 5

    def test_coerce_number(self) -> None:
        p = PipelineParam(type="number")
        assert p.coerce("3.14", "pi") == pytest.approx(3.14)

    def test_coerce_boolean_true_strings(self) -> None:
        p = PipelineParam(type="boolean")
        for truthy in ("true", "True", "TRUE", "1", "yes"):
            assert p.coerce(truthy, "flag") is True

    def test_coerce_boolean_false_string(self) -> None:
        p = PipelineParam(type="boolean")
        assert p.coerce("false", "flag") is False

    def test_coerce_boolean_bool_passthrough(self) -> None:
        p = PipelineParam(type="boolean")
        assert p.coerce(True, "flag") is True
        assert p.coerce(False, "flag") is False

    def test_coerce_list_passthrough(self) -> None:
        p = PipelineParam(type="list")
        v = [1, 2, 3]
        assert p.coerce(v, "items") is v

    def test_coerce_list_rejects_non_list(self) -> None:
        p = PipelineParam(type="list")
        with pytest.raises(ValueError, match="expected a list"):
            p.coerce("not-a-list", "items")

    def test_coerce_object_passthrough(self) -> None:
        p = PipelineParam(type="object")
        v = {"key": "val"}
        assert p.coerce(v, "cfg") is v

    def test_coerce_object_rejects_non_dict(self) -> None:
        p = PipelineParam(type="object")
        with pytest.raises(ValueError, match="expected an object"):
            p.coerce([1, 2], "cfg")

    def test_coerce_integer_invalid_string_raises(self) -> None:
        p = PipelineParam(type="integer")
        with pytest.raises(ValueError, match="cannot coerce"):
            p.coerce("not-a-number", "year")


# ---------------------------------------------------------------------------
# Pipeline.params field parsing
# ---------------------------------------------------------------------------


class TestPipelineParamsParsing:

    def _make_yaml(self, params_block: str = "", task_inputs: str = "") -> str:
        return f"""
pipeline:
  id: test_pipeline
  goal: Test pipeline
{params_block}
  tasks:
    - id: step
      tool: mock
      inputs:
        {task_inputs or "key: value"}
"""

    def test_no_params_block_gives_empty_dict(self) -> None:
        pipeline = Pipeline.from_yaml(self._make_yaml())
        assert pipeline.params == {}

    def test_null_params_coerced_to_empty_dict(self) -> None:
        yaml_text = """
pipeline:
  id: test_pipeline
  goal: Test
  params: null
  tasks:
    - id: step
      tool: mock
      inputs:
        key: value
"""
        pipeline = Pipeline.from_yaml(yaml_text)
        assert pipeline.params == {}

    def test_params_block_parsed_correctly(self) -> None:
        yaml_text = """
pipeline:
  id: fetch_10k
  goal: Fetch 10-K for {{params.ticker}}
  params:
    ticker:
      type: string
      description: Stock ticker symbol
    fiscal_year:
      type: integer
      default: 2024
  tasks:
    - id: fetch
      tool: fetch_data
      inputs:
        ticker: "{{params.ticker}}"
        year: "{{params.fiscal_year}}"
"""
        pipeline = Pipeline.from_yaml(yaml_text)
        assert "ticker" in pipeline.params
        assert "fiscal_year" in pipeline.params
        assert pipeline.params["ticker"].required is True
        assert pipeline.params["fiscal_year"].required is False
        assert pipeline.params["fiscal_year"].default == 2024

    def test_params_refs_in_goal_are_valid(self) -> None:
        yaml_text = """
pipeline:
  id: fetch_10k
  goal: "Fetch {{params.company_name}} 10-K for FY{{params.fiscal_year}}"
  params:
    company_name:
      type: string
    fiscal_year:
      type: integer
  tasks:
    - id: fetch
      tool: fetch_data
      inputs:
        company: "{{params.company_name}}"
"""
        pipeline = Pipeline.from_yaml(yaml_text)
        assert pipeline.goal == "Fetch {{params.company_name}} 10-K for FY{{params.fiscal_year}}"


# ---------------------------------------------------------------------------
# params_refs_exist validator
# ---------------------------------------------------------------------------


class TestParamsRefsExist:

    def test_undeclared_param_in_task_input_raises(self) -> None:
        yaml_text = """
pipeline:
  id: bad_pipeline
  goal: Test
  params:
    ticker:
      type: string
  tasks:
    - id: step
      tool: mock
      inputs:
        company: "{{params.company_name}}"
"""
        with pytest.raises(ValidationError, match="undeclared param.*company_name"):
            Pipeline.from_yaml(yaml_text)

    def test_undeclared_param_in_goal_raises(self) -> None:
        yaml_text = """
pipeline:
  id: bad_pipeline
  goal: "Fetch {{params.ticker}} for {{params.unknown_param}}"
  params:
    ticker:
      type: string
  tasks:
    - id: step
      tool: mock
      inputs:
        key: value
"""
        with pytest.raises(ValidationError, match="undeclared param.*unknown_param"):
            Pipeline.from_yaml(yaml_text)

    def test_params_bare_in_task_raises(self) -> None:
        yaml_text = """
pipeline:
  id: bad_pipeline
  goal: Test
  params: {}
  tasks:
    - id: step
      tool: mock
      inputs:
        key: "{{params}}"
"""
        with pytest.raises(ValidationError, match="without a key name"):
            Pipeline.from_yaml(yaml_text)

    def test_all_declared_params_passes(self) -> None:
        yaml_text = """
pipeline:
  id: good_pipeline
  goal: "Process {{params.company}}"
  params:
    company:
      type: string
    year:
      type: integer
      default: 2024
  tasks:
    - id: step
      tool: mock
      inputs:
        name: "{{params.company}}"
        yr: "{{params.year}}"
"""
        pipeline = Pipeline.from_yaml(yaml_text)
        assert len(pipeline.params) == 2


# ---------------------------------------------------------------------------
# upstream_task_ids — params refs excluded
# ---------------------------------------------------------------------------


class TestUpstreamTaskIdsWithParams:

    def test_params_ref_not_treated_as_task_dep(self) -> None:
        yaml_text = """
pipeline:
  id: pipe
  goal: Test
  params:
    ticker:
      type: string
  tasks:
    - id: step
      tool: mock
      inputs:
        ticker: "{{params.ticker}}"
"""
        pipeline = Pipeline.from_yaml(yaml_text)
        task = pipeline.tasks[0]
        assert task.upstream_task_ids() == set()

    def test_params_and_task_refs_combined(self) -> None:
        yaml_text = """
pipeline:
  id: pipe
  goal: Test
  params:
    ticker:
      type: string
  tasks:
    - id: fetch
      tool: fetch_data
      inputs:
        ticker: "{{params.ticker}}"

    - id: process
      tool: mock
      inputs:
        data: "{{fetch.output}}"
        label: "{{params.ticker}}"
"""
        pipeline = Pipeline.from_yaml(yaml_text)
        process_task = pipeline.task_map()["process"]
        assert process_task.upstream_task_ids() == {"fetch"}
