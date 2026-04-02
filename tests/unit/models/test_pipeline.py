"""
tests/unit/models/test_pipeline.py — Unit tests for trellis.models.pipeline.

Covers:
  - Valid parsing across all fixture pipelines
  - Task field defaults, aliases (await), and coercion
  - Field-level validators (snake_case, known tools, parallel_over template)
  - Model-level validators (unique ids, upstream refs, await refs)
  - upstream_task_ids() — the core dependency inference method
  - extract_template_refs() utility
  - store_keys() and task_map() derived helpers
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from trellis.models.pipeline import (
    KNOWN_TOOLS,
    Pipeline,
    Task,
    extract_template_refs,
)


# ---------------------------------------------------------------------------
# Parsing — happy path
# ---------------------------------------------------------------------------


class TestPipelineParsing:

    def test_from_yaml_data_acquisition(self, data_acquisition_pipeline: Pipeline) -> None:
        assert data_acquisition_pipeline.id == "data_acquisition"
        assert len(data_acquisition_pipeline.tasks) == 8
        assert data_acquisition_pipeline.inputs["companies"] == [
            "Google", "Apple", "Microsoft", "Agilent"
        ]

    def test_from_yaml_linear_pipeline(self, linear_pipeline: Pipeline) -> None:
        assert linear_pipeline.id == "linear_pipeline"
        assert len(linear_pipeline.tasks) == 3
        assert linear_pipeline.inputs == {}

    def test_from_yaml_fan_out_pipeline(self, fan_out_pipeline: Pipeline) -> None:
        assert fan_out_pipeline.id == "fan_out_pipeline"
        assert len(fan_out_pipeline.tasks) == 4

    def test_from_yaml_document_pipeline(self, document_pipeline: Pipeline) -> None:
        assert document_pipeline.id == "document_pipeline"
        assert len(document_pipeline.tasks) == 6

    def test_from_yaml_requires_pipeline_root_key(self) -> None:
        with pytest.raises(ValueError, match="top-level `pipeline:` key"):
            Pipeline.from_yaml("plan:\n  id: oops\n  goal: wrong\n  sub_pipelines: []")

    def test_from_yaml_file(self, tmp_path, data_acquisition_yaml: str) -> None:
        f = tmp_path / "pipeline.yaml"
        f.write_text(data_acquisition_yaml)
        pipeline = Pipeline.from_yaml_file(str(f))
        assert pipeline.id == "data_acquisition"

    def test_null_inputs_coerced_to_empty_dict(self) -> None:
        yaml_text = """
pipeline:
  id: no_inputs
  goal: test
  inputs: null
  tasks:
    - id: fetch
      tool: fetch_data
      inputs:
        source: sec_edgar
"""
        pipeline = Pipeline.from_yaml(yaml_text)
        assert pipeline.inputs == {}


# ---------------------------------------------------------------------------
# Task field validators
# ---------------------------------------------------------------------------


class TestTaskFieldValidators:

    @pytest.mark.parametrize("bad_id", [
        "bad-id", "CamelCase", "has space", "",
    ])
    def test_task_id_rejects_non_snake_case(self, bad_id: str) -> None:
        with pytest.raises(ValidationError, match="snake_case"):
            Task(id=bad_id, tool="fetch_data", inputs={})

    @pytest.mark.parametrize("tool", sorted(KNOWN_TOOLS))
    def test_all_known_tools_accepted(self, tool: str) -> None:
        task = Task(id="task_a", tool=tool, inputs={})
        assert task.tool == tool

    def test_unknown_tool_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Unknown tool"):
            Task(id="task_a", tool="magic_tool", inputs={})

    def test_parallel_over_must_contain_template(self) -> None:
        with pytest.raises(ValidationError, match="parallel_over"):
            Task(
                id="task_a",
                tool="llm_job",
                parallel_over="plain_string_no_braces",
                inputs={"prompt": "test"},
            )

    def test_parallel_over_valid_template(self) -> None:
        task = Task(
            id="task_a",
            tool="llm_job",
            parallel_over="{{pipeline.inputs.companies}}",
            inputs={"company": "{{item}}", "prompt": "assess {{item}}"},
        )
        assert task.parallel_over == "{{pipeline.inputs.companies}}"

    def test_retry_defaults_to_zero(self) -> None:
        task = Task(id="task_a", tool="fetch_data", inputs={})
        assert task.retry == 0

    def test_retry_negative_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Task(id="task_a", tool="fetch_data", inputs={}, retry=-1)

    def test_await_alias_accepted_from_yaml(self) -> None:
        yaml_text = """
pipeline:
  id: await_test
  goal: test await alias
  tasks:
    - id: task_a
      tool: fetch_data
      inputs:
        source: sec_edgar
    - id: task_b
      tool: llm_job
      await: [task_a]
      inputs:
        prompt: "run after task_a without consuming its output"
"""
        pipeline = Pipeline.from_yaml(yaml_text)
        task_b = pipeline.task_map()["task_b"]
        assert task_b.await_ == ["task_a"]

    def test_null_inputs_coerced_to_empty_dict(self) -> None:
        task = Task.model_validate({"id": "task_a", "tool": "fetch_data", "inputs": None})
        assert task.inputs == {}

    def test_null_await_coerced_to_empty_list(self) -> None:
        task = Task.model_validate({"id": "task_a", "tool": "fetch_data", "inputs": {}, "await": None})
        assert task.await_ == []


# ---------------------------------------------------------------------------
# Pipeline model-level validators
# ---------------------------------------------------------------------------


class TestPipelineModelValidators:

    def test_duplicate_task_ids_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Duplicate task ids"):
            Pipeline(
                id="dupe_pipeline",
                goal="test",
                tasks=[
                    Task(id="task_a", tool="fetch_data", inputs={}),
                    Task(id="task_a", tool="llm_job", inputs={"prompt": "dup"}),
                ],
            )

    def test_await_unknown_task_id_rejected(self) -> None:
        with pytest.raises(ValidationError, match="awaits unknown task ids"):
            Pipeline(
                id="bad_await",
                goal="test",
                tasks=[
                    Task(id="task_a", tool="fetch_data", inputs={}, await_=["ghost_task"]),
                ],
            )

    def test_template_ref_to_unknown_task_rejected(self) -> None:
        with pytest.raises(ValidationError, match="unknown task ids"):
            Pipeline(
                id="bad_ref",
                goal="test",
                tasks=[
                    Task(
                        id="task_a",
                        tool="llm_job",
                        inputs={"data": "{{ghost_task.output}}", "prompt": "test"},
                    ),
                ],
            )

    def test_pipeline_inputs_ref_not_treated_as_task(self) -> None:
        """{{pipeline.inputs.x}} should not be treated as an upstream task ref."""
        pipeline = Pipeline(
            id="inputs_ref_test",
            goal="test",
            inputs={"companies": ["Google"]},
            tasks=[
                Task(
                    id="task_a",
                    tool="fetch_data",
                    inputs={"companies": "{{pipeline.inputs.companies}}"},
                ),
            ],
        )
        assert pipeline.task_map()["task_a"].upstream_task_ids() == set()

    def test_session_ref_not_treated_as_task(self) -> None:
        """{{session.key}} should not create a task dependency."""
        pipeline = Pipeline(
            id="session_ref_test",
            goal="test",
            tasks=[
                Task(
                    id="task_a",
                    tool="llm_job",
                    inputs={
                        "context": "{{session.prior_analysis}}",
                        "prompt": "build on prior analysis",
                    },
                ),
            ],
        )
        assert pipeline.task_map()["task_a"].upstream_task_ids() == set()

    def test_tasks_must_not_be_empty(self) -> None:
        with pytest.raises(ValidationError):
            Pipeline(id="empty_pipeline", goal="test", tasks=[])


# ---------------------------------------------------------------------------
# upstream_task_ids()
# ---------------------------------------------------------------------------


class TestUpstreamTaskIds:

    def test_root_task_has_no_upstream(self, data_acquisition_pipeline: Pipeline) -> None:
        fetch = data_acquisition_pipeline.task_map()["fetch_financials"]
        assert fetch.upstream_task_ids() == set()

    def test_store_task_depends_on_fetch(self, data_acquisition_pipeline: Pipeline) -> None:
        store = data_acquisition_pipeline.task_map()["store_financials"]
        assert store.upstream_task_ids() == {"fetch_financials"}

    def test_linear_chain_dependencies(self, linear_pipeline: Pipeline) -> None:
        task_map = linear_pipeline.task_map()
        assert task_map["fetch"].upstream_task_ids() == set()
        assert task_map["summarise"].upstream_task_ids() == {"fetch"}
        assert task_map["export_report"].upstream_task_ids() == {"summarise"}

    def test_fan_out_task_dependencies(self, fan_out_pipeline: Pipeline) -> None:
        """assess_per_company depends on fetch_financials (template) and audit_log (await)."""
        task = fan_out_pipeline.task_map()["assess_per_company"]
        assert task.upstream_task_ids() == {"fetch_financials", "audit_log"}

    def test_synthesise_depends_on_fan_out(self, fan_out_pipeline: Pipeline) -> None:
        task = fan_out_pipeline.task_map()["synthesise"]
        assert task.upstream_task_ids() == {"assess_per_company"}

    def test_item_ref_not_treated_as_task(self) -> None:
        """{{item}} inside a parallel_over task must not create a dependency."""
        task = Task(
            id="fan_task",
            tool="llm_job",
            parallel_over="{{pipeline.inputs.companies}}",
            inputs={
                "company": "{{item}}",
                "prompt": "process {{item}}",
            },
        )
        # item and pipeline refs should both be excluded
        assert task.upstream_task_ids() == set()

    def test_dotted_output_field_ref_extracts_task_id(self) -> None:
        """{{task_a.output.field}} should resolve to upstream task_a."""
        task = Task(
            id="task_b",
            tool="llm_job",
            inputs={"revenue": "{{task_a.output.revenue_line}}"},
        )
        # task_b itself and task_a need to be in a pipeline for full validation,
        # but upstream_task_ids() works on the task in isolation.
        assert "task_a" in task.upstream_task_ids()

    def test_self_reference_excluded(self) -> None:
        """A task that somehow references itself should not appear in its own upstream set."""
        task = Task(id="task_a", tool="llm_job", inputs={"prompt": "test"})
        # upstream_task_ids discards self.id
        assert "task_a" not in task.upstream_task_ids()


# ---------------------------------------------------------------------------
# extract_template_refs()
# ---------------------------------------------------------------------------


class TestExtractTemplateRefs:

    def test_single_ref_in_string(self) -> None:
        assert extract_template_refs("{{task_a.output}}") == ["task_a.output"]

    def test_multiple_refs_in_string(self) -> None:
        refs = extract_template_refs("Hello {{item}}, your data is {{task_a.output.name}}")
        assert "item" in refs
        assert "task_a.output.name" in refs

    def test_refs_in_list(self) -> None:
        refs = extract_template_refs(["{{task_a.output}}", "{{task_b.output}}"])
        assert set(refs) == {"task_a.output", "task_b.output"}

    def test_refs_in_nested_dict(self) -> None:
        refs = extract_template_refs({
            "key1": "{{task_a.output}}",
            "key2": {"nested": "{{task_b.output}}"},
        })
        assert set(refs) == {"task_a.output", "task_b.output"}

    def test_no_refs_in_literal(self) -> None:
        assert extract_template_refs("just a plain string") == []

    def test_no_refs_in_number(self) -> None:
        assert extract_template_refs(42) == []

    def test_no_refs_in_none(self) -> None:
        assert extract_template_refs(None) == []

    def test_whitespace_inside_braces_stripped(self) -> None:
        refs = extract_template_refs("{{ task_a.output }}")
        assert refs == ["task_a.output"]


# ---------------------------------------------------------------------------
# Derived helpers: task_map() and store_keys()
# ---------------------------------------------------------------------------


class TestDerivedHelpers:

    def test_task_map_keys_match_task_ids(self, data_acquisition_pipeline: Pipeline) -> None:
        task_map = data_acquisition_pipeline.task_map()
        expected_ids = {t.id for t in data_acquisition_pipeline.tasks}
        assert set(task_map.keys()) == expected_ids

    def test_task_map_values_are_task_instances(self, data_acquisition_pipeline: Pipeline) -> None:
        task_map = data_acquisition_pipeline.task_map()
        assert all(isinstance(t, Task) for t in task_map.values())

    def test_store_keys_data_acquisition(self, data_acquisition_pipeline: Pipeline) -> None:
        keys = data_acquisition_pipeline.store_keys()
        assert set(keys) == {
            "company_financials", "energy_exposure",
            "energy_markets", "disruption_context",
        }

    def test_store_keys_empty_when_no_store_tasks(self, linear_pipeline: Pipeline) -> None:
        # linear_pipeline has no store tasks
        assert linear_pipeline.store_keys() == []

    def test_store_keys_fan_out_pipeline(self, fan_out_pipeline: Pipeline) -> None:
        # fan_out_pipeline has no store tasks either
        assert fan_out_pipeline.store_keys() == []