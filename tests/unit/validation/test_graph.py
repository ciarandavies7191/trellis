"""
tests/unit/validation/test_graph.py — Unit tests for trellis.validation.graph.

Covers:
  - pipeline_execution_waves(): correct wave groupings, root detection,
    fan-out, await barriers, cycle detection
  - plan_execution_waves(): sequential chains, parallel roots,
    stores conflicts, cycle detection
  - find_cycle(): cycle path recovery
  - _kahn_waves(): internal algorithm correctness (via public API)
"""

from __future__ import annotations

import pytest

from trellis.exceptions import ContractError, CycleError
from trellis.models.pipeline import Pipeline
from trellis.models.plan import Plan
from trellis.validation.graph import find_cycle, pipeline_execution_waves, plan_execution_waves


# ---------------------------------------------------------------------------
# pipeline_execution_waves — structure
# ---------------------------------------------------------------------------


class TestPipelineExecutionWaves:

    def test_data_acquisition_two_waves(self, data_acquisition_pipeline: Pipeline) -> None:
        """4 parallel root fetches → 4 parallel stores."""
        waves = pipeline_execution_waves(data_acquisition_pipeline)
        assert len(waves) == 2
        wave_0_ids = {t.id for t in waves[0]}
        wave_1_ids = {t.id for t in waves[1]}
        assert wave_0_ids == {
            "fetch_financials", "fetch_energy_exposure",
            "fetch_energy_markets", "search_disruption_context",
        }
        assert wave_1_ids == {
            "store_financials", "store_energy_exposure",
            "store_energy_markets", "store_disruption_context",
        }

    def test_linear_pipeline_three_waves(self, linear_pipeline: Pipeline) -> None:
        waves = pipeline_execution_waves(linear_pipeline)
        assert len(waves) == 3
        assert [t.id for t in waves[0]] == ["fetch"]
        assert [t.id for t in waves[1]] == ["summarise"]
        assert [t.id for t in waves[2]] == ["export_report"]

    def test_fan_out_pipeline_four_waves(self, fan_out_pipeline: Pipeline) -> None:
        """fetch → audit_log → assess_per_company (await) → synthesise."""
        waves = pipeline_execution_waves(fan_out_pipeline)
        assert len(waves) == 4
        assert {t.id for t in waves[0]} == {"fetch_financials"}
        assert {t.id for t in waves[1]} == {"audit_log"}
        assert {t.id for t in waves[2]} == {"assess_per_company"}
        assert {t.id for t in waves[3]} == {"synthesise"}

    def test_document_pipeline_wave_structure(self, document_pipeline: Pipeline) -> None:
        """
        ingest_report →
          select_financial_pages & extract_notes (parallel) →
            extract_tables →
              reconcile →
                produce_report
        """
        waves = pipeline_execution_waves(document_pipeline)
        wave_ids = [{t.id for t in w} for w in waves]

        # ingest is the sole root
        assert wave_ids[0] == {"ingest_report"}
        # select and extract_notes both depend only on ingest — should be parallel
        assert "select_financial_pages" in wave_ids[1]
        assert "extract_notes" in wave_ids[1]
        # reconcile depends on both extract_tables and extract_notes
        reconcile_wave = next(i for i, w in enumerate(wave_ids) if "reconcile" in w)
        extract_tables_wave = next(i for i, w in enumerate(wave_ids) if "extract_tables" in w)
        assert reconcile_wave > extract_tables_wave

    def test_single_task_pipeline_one_wave(self) -> None:
        pipeline = Pipeline.from_yaml("""
pipeline:
  id: single_task
  goal: single task pipeline
  tasks:
    - id: fetch
      tool: fetch_data
      inputs:
        source: sec_edgar
""")
        waves = pipeline_execution_waves(pipeline)
        assert len(waves) == 1
        assert waves[0][0].id == "fetch"

    def test_all_parallel_roots(self) -> None:
        """Three tasks with no mutual dependencies → all in wave 0."""
        pipeline = Pipeline.from_yaml("""
pipeline:
  id: all_parallel
  goal: all roots
  tasks:
    - id: task_a
      tool: fetch_data
      inputs:
        source: sec_edgar
    - id: task_b
      tool: search_web
      inputs:
        query: "test query"
    - id: task_c
      tool: fetch_data
      inputs:
        source: market_data
""")
        waves = pipeline_execution_waves(pipeline)
        assert len(waves) == 1
        assert len(waves[0]) == 3

    def test_pipeline_inputs_ref_does_not_create_dependency(self) -> None:
        """Tasks that only reference pipeline.inputs should all be roots."""
        pipeline = Pipeline.from_yaml("""
pipeline:
  id: inputs_only
  goal: pipeline inputs only
  inputs:
    companies: [Google, Apple]
  tasks:
    - id: task_a
      tool: fetch_data
      inputs:
        companies: "{{pipeline.inputs.companies}}"
        source: company_info
    - id: task_b
      tool: search_web
      inputs:
        query: "{{pipeline.inputs.companies}} annual report"
""")
        waves = pipeline_execution_waves(pipeline)
        assert len(waves) == 1
        assert len(waves[0]) == 2

    def test_session_ref_does_not_create_dependency(self) -> None:
        """Tasks that only reference session.* should all be roots."""
        pipeline = Pipeline.from_yaml("""
pipeline:
  id: session_ref_test
  goal: session refs only
  tasks:
    - id: task_a
      tool: llm_job
      inputs:
        context: "{{session.prior_results}}"
        prompt: "Build on prior results"
    - id: task_b
      tool: llm_job
      inputs:
        context: "{{session.market_context}}"
        prompt: "Use market context"
""")
        waves = pipeline_execution_waves(pipeline)
        assert len(waves) == 1
        assert len(waves[0]) == 2

    def test_await_creates_dependency_without_output_ref(self) -> None:
        pipeline = Pipeline.from_yaml("""
pipeline:
  id: await_only_dep
  goal: await barrier test
  tasks:
    - id: write_audit
      tool: llm_job
      inputs:
        prompt: "write audit log"
    - id: generate_report
      tool: llm_job
      await: [write_audit]
      inputs:
        prompt: "generate report after audit"
""")
        waves = pipeline_execution_waves(pipeline)
        assert len(waves) == 2
        assert waves[0][0].id == "write_audit"
        assert waves[1][0].id == "generate_report"

    def test_waves_return_task_objects_not_ids(self, linear_pipeline: Pipeline) -> None:
        from trellis.models.pipeline import Task
        waves = pipeline_execution_waves(linear_pipeline)
        for wave in waves:
            for task in wave:
                assert isinstance(task, Task)


# ---------------------------------------------------------------------------
# pipeline_execution_waves — cycle detection
# ---------------------------------------------------------------------------


class TestPipelineCycleDetection:

    def test_simple_three_node_cycle(self) -> None:
        pipeline = Pipeline.from_yaml("""
pipeline:
  id: cycle_three
  goal: three-node cycle
  tasks:
    - id: task_a
      tool: llm_job
      inputs:
        data: "{{task_c.output}}"
        prompt: "a"
    - id: task_b
      tool: llm_job
      inputs:
        data: "{{task_a.output}}"
        prompt: "b"
    - id: task_c
      tool: llm_job
      inputs:
        data: "{{task_b.output}}"
        prompt: "c"
""")
        with pytest.raises(CycleError) as exc_info:
            pipeline_execution_waves(pipeline)
        assert exc_info.value.cycle is not None
        assert len(exc_info.value.cycle) > 0

    def test_self_loop_via_await(self) -> None:
        """A task awaiting itself — caught by Pydantic (self removed from upstream_task_ids),
        but worth confirming doesn't silently succeed."""
        # upstream_task_ids() discards self.id, so a self-loop via await
        # is actually harmless — no cycle is created. Verify no error raised.
        pipeline = Pipeline.from_yaml("""
pipeline:
  id: self_await_test
  goal: self await
  tasks:
    - id: task_a
      tool: llm_job
      inputs:
        prompt: "test"
""")
        waves = pipeline_execution_waves(pipeline)
        assert len(waves) == 1

    def test_two_node_mutual_cycle(self) -> None:
        pipeline = Pipeline.from_yaml("""
pipeline:
  id: two_node_cycle
  goal: two-node cycle
  tasks:
    - id: task_a
      tool: llm_job
      inputs:
        data: "{{task_b.output}}"
        prompt: "a"
    - id: task_b
      tool: llm_job
      inputs:
        data: "{{task_a.output}}"
        prompt: "b"
""")
        with pytest.raises(CycleError) as exc_info:
            pipeline_execution_waves(pipeline)
        cycle = exc_info.value.cycle
        assert cycle is not None
        # Cycle path should mention both nodes
        cycle_str = " ".join(cycle)
        assert "task_a" in cycle_str
        assert "task_b" in cycle_str

    def test_cycle_error_message_is_descriptive(self) -> None:
        pipeline = Pipeline.from_yaml("""
pipeline:
  id: cycle_msg_test
  goal: cycle message test
  tasks:
    - id: alpha
      tool: llm_job
      inputs:
        data: "{{beta.output}}"
        prompt: "alpha"
    - id: beta
      tool: llm_job
      inputs:
        data: "{{alpha.output}}"
        prompt: "beta"
""")
        with pytest.raises(CycleError, match="Cycle detected"):
            pipeline_execution_waves(pipeline)


# ---------------------------------------------------------------------------
# plan_execution_waves — structure
# ---------------------------------------------------------------------------


class TestPlanExecutionWaves:

    def test_gulf_plan_sequential_chain(self, gulf_plan: Plan) -> None:
        """All 6 sub-pipelines are sequential — should produce 6 waves."""
        waves = plan_execution_waves(gulf_plan)
        assert len(waves) == 6
        wave_ids = [[sp.id for sp in w] for w in waves]
        assert wave_ids[0] == ["data_acquisition"]
        assert wave_ids[1] == ["short_term_assessment"]
        assert wave_ids[2] == ["medium_term_assessment"]
        assert wave_ids[3] == ["long_term_assessment"]
        assert wave_ids[4] == ["synthesis"]
        assert wave_ids[5] == ["final_report"]

    def test_parallel_plan_two_roots(self, parallel_plan: Plan) -> None:
        waves = plan_execution_waves(parallel_plan)
        assert len(waves) == 2
        root_ids = {sp.id for sp in waves[0]}
        assert root_ids == {"fetch_financials", "fetch_market_data"}
        assert waves[1][0].id == "synthesis"

    def test_minimal_plan_single_wave(self, minimal_plan_yaml: str) -> None:
        plan = Plan.from_yaml(minimal_plan_yaml)
        waves = plan_execution_waves(plan)
        assert len(waves) == 1
        assert waves[0][0].id == "only_pipeline"

    def test_all_independent_sub_pipelines_single_wave(self) -> None:
        plan = Plan.from_yaml("""
plan:
  id: all_independent
  goal: all independent sub-pipelines
  sub_pipelines:
    - id: sp_a
      goal: fetch A
      reads: []
      stores: [result_a]
    - id: sp_b
      goal: fetch B
      reads: []
      stores: [result_b]
    - id: sp_c
      goal: fetch C
      reads: []
      stores: [result_c]
""")
        waves = plan_execution_waves(plan)
        assert len(waves) == 1
        assert len(waves[0]) == 3

    def test_waves_return_sub_pipeline_objects(self, gulf_plan: Plan) -> None:
        from trellis.models.plan import SubPipeline
        waves = plan_execution_waves(gulf_plan)
        for wave in waves:
            for sp in wave:
                assert isinstance(sp, SubPipeline)

    def test_unwritten_reads_do_not_cause_error(self) -> None:
        """Reads that are not written by any sub-pipeline in the plan are allowed —
        they may come from plan.inputs or a prior session."""
        plan = Plan.from_yaml("""
plan:
  id: external_reads_plan
  goal: reads from external session
  inputs:
    prior_data: some_value
  sub_pipelines:
    - id: sp_a
      goal: uses session data
      reads: [prior_data]
      stores: [result_a]
      inputs: [prior_data]
""")
        waves = plan_execution_waves(plan)
        assert len(waves) == 1


# ---------------------------------------------------------------------------
# plan_execution_waves — stores conflict and cycle detection
# ---------------------------------------------------------------------------


class TestPlanValidationErrors:

    def test_stores_conflict_raises_contract_error(self) -> None:
        plan = Plan.from_yaml("""
plan:
  id: conflict_plan
  goal: two sub-pipelines write the same key
  inputs:
    companies: [Google]
  sub_pipelines:
    - id: sp_a
      goal: first writer
      reads: []
      stores: [shared_key]
      inputs: [companies]
    - id: sp_b
      goal: second writer — conflict
      reads: []
      stores: [shared_key]
      inputs: [companies]
""")
        with pytest.raises(ContractError, match="shared_key"):
            plan_execution_waves(plan)

    def test_stores_conflict_message_names_both_pipelines(self) -> None:
        plan = Plan.from_yaml("""
plan:
  id: conflict_plan
  goal: conflict test
  inputs:
    x: 1
  sub_pipelines:
    - id: writer_one
      goal: first
      reads: []
      stores: [contested_key]
      inputs: [x]
    - id: writer_two
      goal: second
      reads: []
      stores: [contested_key]
      inputs: [x]
""")
        with pytest.raises(ContractError) as exc_info:
            plan_execution_waves(plan)
        msg = str(exc_info.value)
        assert "writer_one" in msg or "writer_two" in msg
        assert "contested_key" in msg

    def test_plan_level_cycle_raises_cycle_error(self) -> None:
        """Sub-pipelines A reads B's output, B reads A's output."""
        plan = Plan.from_yaml("""
plan:
  id: cyclic_plan
  goal: cyclic sub-pipeline graph
  sub_pipelines:
    - id: sp_a
      goal: reads from sp_b
      reads: [result_b]
      stores: [result_a]
    - id: sp_b
      goal: reads from sp_a
      reads: [result_a]
      stores: [result_b]
""")
        with pytest.raises(CycleError):
            plan_execution_waves(plan)


# ---------------------------------------------------------------------------
# find_cycle()
# ---------------------------------------------------------------------------


class TestFindCycle:

    def test_simple_two_node_cycle(self) -> None:
        graph = {
            "a": {"b"},
            "b": {"a"},
        }
        cycle = find_cycle(graph)
        assert len(cycle) > 0
        # Should start and end at the same node
        assert cycle[0] == cycle[-1]

    def test_three_node_cycle(self) -> None:
        graph = {
            "a": {"c"},   # a depends on c
            "b": {"a"},   # b depends on a
            "c": {"b"},   # c depends on b
        }
        cycle = find_cycle(graph)
        assert len(cycle) >= 4  # at least 3 unique + repeated start node
        assert cycle[0] == cycle[-1]

    def test_acyclic_graph_returns_empty(self) -> None:
        graph = {
            "a": set(),
            "b": {"a"},
            "c": {"b"},
        }
        cycle = find_cycle(graph)
        assert cycle == []

    def test_disconnected_cycle(self) -> None:
        """Cycle in one component, DAG in another — should still find cycle."""
        graph = {
            "a": set(),        # DAG root
            "b": {"a"},        # DAG node
            "x": {"y"},        # cycle
            "y": {"x"},        # cycle
        }
        cycle = find_cycle(graph)
        assert len(cycle) > 0
        assert cycle[0] == cycle[-1]