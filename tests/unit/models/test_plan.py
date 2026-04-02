"""
tests/unit/models/test_plan.py — Unit tests for trellis.models.plan.

Covers:
  - Valid parsing (from_yaml, from_yaml_file)
  - Field defaults and coercion
  - Field-level validators (snake_case id)
  - Model-level validators (unique ids, inputs key references)
  - Edge cases (null lists, minimal plan)
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from trellis.models.plan import Plan, SubPipeline


# ---------------------------------------------------------------------------
# Parsing — happy path
# ---------------------------------------------------------------------------


class TestPlanParsing:

    def test_from_yaml_gulf_plan(self, gulf_plan: Plan) -> None:
        assert gulf_plan.id == "gulf_disruption_assessment"
        assert len(gulf_plan.sub_pipelines) == 6
        assert gulf_plan.inputs["companies"] == ["Google", "Apple", "Microsoft", "Agilent"]
        assert gulf_plan.inputs["year"] == 2025

    def test_from_yaml_minimal_plan(self, minimal_plan_yaml: str) -> None:
        plan = Plan.from_yaml(minimal_plan_yaml)
        assert plan.id == "minimal_plan"
        assert len(plan.sub_pipelines) == 1
        assert plan.inputs == {}

    def test_from_yaml_requires_plan_root_key(self) -> None:
        with pytest.raises(ValueError, match="top-level `plan:` key"):
            Plan.from_yaml("pipeline:\n  id: oops\n  goal: wrong\n  tasks: []")

    def test_from_yaml_rejects_empty_document(self) -> None:
        with pytest.raises((ValueError, ValidationError)):
            Plan.from_yaml("")

    def test_from_yaml_file(self, tmp_path, gulf_plan_yaml: str) -> None:
        f = tmp_path / "plan.yaml"
        f.write_text(gulf_plan_yaml)
        plan = Plan.from_yaml_file(str(f))
        assert plan.id == "gulf_disruption_assessment"


# ---------------------------------------------------------------------------
# SubPipeline fields
# ---------------------------------------------------------------------------


class TestSubPipelineFields:

    def test_reads_stores_inputs_populated(self, gulf_plan: Plan) -> None:
        data_acq = gulf_plan.sub_pipelines[0]
        assert data_acq.id == "data_acquisition"
        assert data_acq.reads == []
        assert set(data_acq.stores) == {
            "company_financials", "energy_exposure",
            "energy_markets", "disruption_context",
        }
        assert set(data_acq.inputs) == {"companies", "year"}

    def test_reads_stores_inputs_default_to_empty_list(self) -> None:
        sp = SubPipeline(id="my_pipeline", goal="do something")
        assert sp.reads == []
        assert sp.stores == []
        assert sp.inputs == []

    def test_null_reads_coerced_to_empty_list(self) -> None:
        yaml_text = """
plan:
  id: null_test_plan
  goal: test
  sub_pipelines:
    - id: sp_a
      goal: test
      reads: null
      stores: null
      inputs: null
"""
        plan = Plan.from_yaml(yaml_text)
        sp = plan.sub_pipelines[0]
        assert sp.reads == []
        assert sp.stores == []
        assert sp.inputs == []

    def test_final_report_has_empty_stores(self, gulf_plan: Plan) -> None:
        final = next(sp for sp in gulf_plan.sub_pipelines if sp.id == "final_report")
        assert final.stores == []
        assert final.inputs == []


# ---------------------------------------------------------------------------
# Plan-level field validators
# ---------------------------------------------------------------------------


class TestPlanFieldValidators:

    def test_id_snake_case_valid(self) -> None:
        plan = Plan(
            id="my_valid_plan",
            goal="test",
            sub_pipelines=[SubPipeline(id="sp_a", goal="step a")],
        )
        assert plan.id == "my_valid_plan"

    @pytest.mark.parametrize("bad_id", [
        "bad-id",
        "CamelCase",
        "has space",
        "123_starts_with_digit",
        "",
    ])
    def test_id_rejects_non_snake_case(self, bad_id: str) -> None:
        with pytest.raises(ValidationError, match="snake_case"):
            Plan(
                id=bad_id,
                goal="test",
                sub_pipelines=[SubPipeline(id="sp_a", goal="step a")],
            )

    def test_sub_pipeline_id_rejects_non_snake_case(self) -> None:
        with pytest.raises(ValidationError, match="snake_case"):
            Plan(
                id="valid_plan",
                goal="test",
                sub_pipelines=[SubPipeline(id="bad-id", goal="step a")],
            )

    def test_null_inputs_coerced_to_empty_dict(self) -> None:
        yaml_text = """
plan:
  id: no_inputs_plan
  goal: test plan
  inputs: null
  sub_pipelines:
    - id: sp_a
      goal: do something
"""
        plan = Plan.from_yaml(yaml_text)
        assert plan.inputs == {}


# ---------------------------------------------------------------------------
# Plan-level model validators
# ---------------------------------------------------------------------------


class TestPlanModelValidators:

    def test_duplicate_sub_pipeline_ids_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Duplicate sub-pipeline ids"):
            Plan(
                id="dupe_plan",
                goal="test",
                sub_pipelines=[
                    SubPipeline(id="sp_a", goal="first"),
                    SubPipeline(id="sp_a", goal="duplicate"),
                ],
            )

    def test_sub_pipeline_inputs_must_exist_in_plan_inputs(self) -> None:
        with pytest.raises(ValidationError, match="unknown plan input keys"):
            Plan(
                id="bad_ref_plan",
                goal="test",
                inputs={"companies": ["Google"]},
                sub_pipelines=[
                    SubPipeline(
                        id="sp_a",
                        goal="step",
                        inputs=["companies", "nonexistent_key"],
                    )
                ],
            )

    def test_sub_pipeline_inputs_all_valid(self) -> None:
        plan = Plan(
            id="valid_ref_plan",
            goal="test",
            inputs={"companies": ["Google"], "year": 2025},
            sub_pipelines=[
                SubPipeline(
                    id="sp_a",
                    goal="step",
                    inputs=["companies", "year"],
                )
            ],
        )
        assert plan.sub_pipelines[0].inputs == ["companies", "year"]

    def test_sub_pipelines_must_not_be_empty(self) -> None:
        with pytest.raises(ValidationError):
            Plan(id="empty_plan", goal="test", sub_pipelines=[])

    def test_gulf_plan_has_correct_dependency_chain(self, gulf_plan: Plan) -> None:
        """Verify reads/stores declarations match the expected sequential chain."""
        sp_map = {sp.id: sp for sp in gulf_plan.sub_pipelines}

        assert sp_map["data_acquisition"].reads == []
        assert "company_financials" in sp_map["short_term_assessment"].reads
        assert "credit_short" in sp_map["medium_term_assessment"].reads
        assert "credit_medium" in sp_map["long_term_assessment"].reads
        assert "credit_long" in sp_map["synthesis"].reads
        assert "liquidity_analysis" in sp_map["final_report"].reads