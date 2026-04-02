"""
tests/unit/validation/test_contract.py — Unit tests for trellis.validation.contract.

Covers:
  - validate_contract(): returns empty list for fully valid pipelines
  - _check_stores():  missing store keys, undeclared store keys
  - _check_reads():   undeclared {{session.key}} references
  - _check_inputs():  undeclared {{pipeline.inputs.key}} references
  - assert_contract(): raises ContractError with full violation summary
  - Multiple simultaneous violations collected in one pass
  - ViolationKind enum values on returned violations
"""

from __future__ import annotations

import pytest

from trellis.exceptions import ContractError
from trellis.models.pipeline import Pipeline
from trellis.models.plan import SubPipeline
from trellis.validation.contract import (
    ContractViolation,
    ViolationKind,
    assert_contract,
    validate_contract,
)


# ---------------------------------------------------------------------------
# Helpers — build minimal valid objects inline
# ---------------------------------------------------------------------------


def make_sub_pipeline(
    id: str = "test_pipeline",
    reads: list[str] | None = None,
    stores: list[str] | None = None,
    inputs: list[str] | None = None,
) -> SubPipeline:
    return SubPipeline(
        id=id,
        goal="Test sub-pipeline",
        reads=reads or [],
        stores=stores or [],
        inputs=inputs or [],
    )


def make_pipeline(yaml: str) -> Pipeline:
    return Pipeline.from_yaml(yaml)


# ---------------------------------------------------------------------------
# Fully valid pipelines — no violations
# ---------------------------------------------------------------------------


class TestValidContractReturnsEmpty:

    def test_data_acquisition_pipeline_valid(
        self,
        data_acquisition_pipeline: Pipeline,
    ) -> None:
        sp = make_sub_pipeline(
            id="data_acquisition",
            stores=["company_financials", "energy_exposure",
                    "energy_markets", "disruption_context"],
            inputs=["companies", "year"],
        )
        violations = validate_contract(data_acquisition_pipeline, sp)
        assert violations == []

    def test_pipeline_with_no_stores_and_empty_declaration(self) -> None:
        pipeline = make_pipeline("""
pipeline:
  id: no_stores_pipeline
  goal: pipeline with no store tasks
  inputs:
    companies: [Google, Apple]
  tasks:
    - id: fetch
      tool: fetch_data
      inputs:
        source: company_info
        companies: "{{pipeline.inputs.companies}}"
    - id: analyse
      tool: llm_job
      inputs:
        data: "{{fetch.output}}"
        prompt: "Analyse the data"
""")
        sp = make_sub_pipeline(stores=[], inputs=["companies"])
        violations = validate_contract(pipeline, sp)
        assert violations == []

    def test_pipeline_with_session_reads_declared(self) -> None:
        pipeline = make_pipeline("""
pipeline:
  id: session_reads_pipeline
  goal: uses session blackboard
  tasks:
    - id: analyse
      tool: llm_job
      inputs:
        prior: "{{session.prior_results}}"
        context: "{{session.market_context}}"
        prompt: "Build on prior analysis"
""")
        sp = make_sub_pipeline(
            reads=["prior_results", "market_context"],
            stores=[],
        )
        violations = validate_contract(pipeline, sp)
        assert violations == []

    def test_pipeline_with_all_inputs_declared(self) -> None:
        pipeline = make_pipeline("""
pipeline:
  id: full_inputs_pipeline
  goal: all inputs declared
  inputs:
    companies: [Google]
    year: 2025
    threshold: 0.05
  tasks:
    - id: fetch
      tool: fetch_data
      inputs:
        companies: "{{pipeline.inputs.companies}}"
        year: "{{pipeline.inputs.year}}"
    - id: analyse
      tool: llm_job
      inputs:
        data: "{{fetch.output}}"
        threshold: "{{pipeline.inputs.threshold}}"
        prompt: "Analyse with threshold"
""")
        sp = make_sub_pipeline(inputs=["companies", "year", "threshold"])
        violations = validate_contract(pipeline, sp)
        assert violations == []


# ---------------------------------------------------------------------------
# Stores contract violations
# ---------------------------------------------------------------------------


class TestStoresContract:

    def test_missing_store_key(self) -> None:
        pipeline = make_pipeline("""
pipeline:
  id: missing_store_pipeline
  goal: missing a store task
  tasks:
    - id: fetch
      tool: fetch_data
      inputs:
        source: company_info
    - id: store_financials
      tool: store
      inputs:
        key: company_financials
        value: "{{fetch.output}}"
""")
        sp = make_sub_pipeline(
            stores=["company_financials", "energy_exposure"],  # energy_exposure never written
        )
        violations = validate_contract(pipeline, sp)
        assert len(violations) == 1
        v = violations[0]
        assert v.kind == ViolationKind.MISSING_STORE
        assert v.key == "energy_exposure"
        assert v.task_id is None

    def test_undeclared_store_key(self) -> None:
        pipeline = make_pipeline("""
pipeline:
  id: undeclared_store_pipeline
  goal: writes an undeclared key
  tasks:
    - id: fetch
      tool: fetch_data
      inputs:
        source: company_info
    - id: store_surprise
      tool: store
      inputs:
        key: surprise_key
        value: "{{fetch.output}}"
""")
        sp = make_sub_pipeline(stores=[])  # surprise_key not declared
        violations = validate_contract(pipeline, sp)
        assert len(violations) == 1
        v = violations[0]
        assert v.kind == ViolationKind.UNDECLARED_STORE
        assert v.key == "surprise_key"
        assert v.task_id == "store_surprise"

    def test_multiple_missing_stores(self) -> None:
        pipeline = make_pipeline("""
pipeline:
  id: empty_pipeline
  goal: writes nothing
  tasks:
    - id: fetch
      tool: fetch_data
      inputs:
        source: sec_edgar
""")
        sp = make_sub_pipeline(stores=["key_a", "key_b", "key_c"])
        violations = validate_contract(pipeline, sp)
        missing_keys = {v.key for v in violations if v.kind == ViolationKind.MISSING_STORE}
        assert missing_keys == {"key_a", "key_b", "key_c"}

    def test_multiple_undeclared_stores(self) -> None:
        pipeline = make_pipeline("""
pipeline:
  id: over_writer_pipeline
  goal: writes undeclared keys
  tasks:
    - id: fetch
      tool: fetch_data
      inputs:
        source: sec_edgar
    - id: store_a
      tool: store
      inputs:
        key: extra_a
        value: "{{fetch.output}}"
    - id: store_b
      tool: store
      inputs:
        key: extra_b
        value: "{{fetch.output}}"
""")
        sp = make_sub_pipeline(stores=[])
        violations = validate_contract(pipeline, sp)
        undeclared_keys = {v.key for v in violations if v.kind == ViolationKind.UNDECLARED_STORE}
        assert undeclared_keys == {"extra_a", "extra_b"}

    def test_exact_match_produces_no_violations(self) -> None:
        pipeline = make_pipeline("""
pipeline:
  id: exact_match_pipeline
  goal: exact stores match
  tasks:
    - id: fetch
      tool: fetch_data
      inputs:
        source: sec_edgar
    - id: store_result
      tool: store
      inputs:
        key: result
        value: "{{fetch.output}}"
""")
        sp = make_sub_pipeline(stores=["result"])
        violations = validate_contract(pipeline, sp)
        assert violations == []


# ---------------------------------------------------------------------------
# Reads contract violations
# ---------------------------------------------------------------------------


class TestReadsContract:

    def test_undeclared_session_reference(self) -> None:
        pipeline = make_pipeline("""
pipeline:
  id: session_ref_pipeline
  goal: uses undeclared session key
  tasks:
    - id: analyse
      tool: llm_job
      inputs:
        prior: "{{session.prior_results}}"
        prompt: "Build on prior analysis"
""")
        sp = make_sub_pipeline(reads=[])  # prior_results not declared
        violations = validate_contract(pipeline, sp)
        assert len(violations) == 1
        v = violations[0]
        assert v.kind == ViolationKind.UNDECLARED_SESSION
        assert v.key == "prior_results"
        assert v.task_id == "analyse"

    def test_multiple_undeclared_session_references(self) -> None:
        pipeline = make_pipeline("""
pipeline:
  id: multi_session_pipeline
  goal: uses multiple undeclared session keys
  tasks:
    - id: task_a
      tool: llm_job
      inputs:
        data_a: "{{session.key_a}}"
        prompt: "Use key_a"
    - id: task_b
      tool: llm_job
      inputs:
        data_b: "{{session.key_b}}"
        prompt: "Use key_b"
""")
        sp = make_sub_pipeline(reads=[])
        violations = validate_contract(pipeline, sp)
        undeclared = {v.key for v in violations if v.kind == ViolationKind.UNDECLARED_SESSION}
        assert undeclared == {"key_a", "key_b"}

    def test_session_ref_in_parallel_over_checked(self) -> None:
        """{{session.key}} inside parallel_over should also be validated."""
        pipeline = make_pipeline("""
pipeline:
  id: parallel_session_pipeline
  goal: session ref in parallel_over
  tasks:
    - id: fan_task
      tool: llm_job
      parallel_over: "{{session.company_list}}"
      inputs:
        company: "{{item}}"
        prompt: "Assess {{item}}"
""")
        sp = make_sub_pipeline(reads=[])  # company_list not declared
        violations = validate_contract(pipeline, sp)
        keys = {v.key for v in violations if v.kind == ViolationKind.UNDECLARED_SESSION}
        assert "company_list" in keys

    def test_declared_session_refs_produce_no_violations(self) -> None:
        pipeline = make_pipeline("""
pipeline:
  id: declared_session_pipeline
  goal: all session refs declared
  tasks:
    - id: analyse
      tool: llm_job
      inputs:
        financials: "{{session.company_financials}}"
        market: "{{session.energy_markets}}"
        prompt: "Analyse financial exposure"
""")
        sp = make_sub_pipeline(reads=["company_financials", "energy_markets"])
        violations = validate_contract(pipeline, sp)
        assert violations == []

    def test_partial_reads_declaration(self) -> None:
        """One session key declared, one not — only the undeclared one flagged."""
        pipeline = make_pipeline("""
pipeline:
  id: partial_reads_pipeline
  goal: partial reads
  tasks:
    - id: analyse
      tool: llm_job
      inputs:
        good: "{{session.declared_key}}"
        bad: "{{session.missing_key}}"
        prompt: "Analyse both"
""")
        sp = make_sub_pipeline(reads=["declared_key"])
        violations = validate_contract(pipeline, sp)
        assert len(violations) == 1
        assert violations[0].key == "missing_key"
        assert violations[0].kind == ViolationKind.UNDECLARED_SESSION


# ---------------------------------------------------------------------------
# Inputs contract violations
# ---------------------------------------------------------------------------


class TestInputsContract:

    def test_undeclared_pipeline_input_reference(self) -> None:
        pipeline = make_pipeline("""
pipeline:
  id: missing_input_pipeline
  goal: references undeclared input
  inputs:
    companies: [Google]
  tasks:
    - id: fetch
      tool: fetch_data
      inputs:
        companies: "{{pipeline.inputs.companies}}"
        year: "{{pipeline.inputs.year}}"
""")
        sp = make_sub_pipeline(inputs=["companies"])
        violations = validate_contract(pipeline, sp)
        assert len(violations) == 1
        v = violations[0]
        assert v.kind == ViolationKind.UNDECLARED_INPUT
        assert v.key == "year"
        assert v.task_id == "fetch"

    def test_multiple_undeclared_inputs(self) -> None:
        pipeline = make_pipeline("""
pipeline:
  id: multi_missing_input_pipeline
  goal: references multiple undeclared inputs
  tasks:
    - id: fetch
      tool: fetch_data
      inputs:
        companies: "{{pipeline.inputs.companies}}"
        year: "{{pipeline.inputs.year}}"
        threshold: "{{pipeline.inputs.threshold}}"
""")
        sp = make_sub_pipeline()
        violations = validate_contract(pipeline, sp)
        undeclared = {v.key for v in violations if v.kind == ViolationKind.UNDECLARED_INPUT}
        assert undeclared == {"companies", "year", "threshold"}

    def test_all_inputs_declared_no_violation(self) -> None:
        pipeline = make_pipeline("""
pipeline:
  id: all_inputs_declared
  goal: all inputs declared
  inputs:
    companies: [Google]
    year: 2025
  tasks:
    - id: fetch
      tool: fetch_data
      inputs:
        companies: "{{pipeline.inputs.companies}}"
        year: "{{pipeline.inputs.year}}"
""")
        sp = make_sub_pipeline(inputs=["companies", "year"])
        violations = validate_contract(pipeline, sp)
        assert violations == []

    def test_no_input_refs_no_violation(self) -> None:
        pipeline = make_pipeline("""
pipeline:
  id: no_input_refs
  goal: no pipeline.inputs references
  tasks:
    - id: fetch
      tool: fetch_data
      inputs:
        source: sec_edgar
""")
        sp = make_sub_pipeline()
        violations = validate_contract(pipeline, sp)
        assert violations == []


# ---------------------------------------------------------------------------
# Multiple simultaneous violations
# ---------------------------------------------------------------------------


class TestMultipleViolations:

    def test_all_three_violation_types_in_one_pipeline(self) -> None:
        pipeline = make_pipeline("""
pipeline:
  id: bad_pipeline
  goal: violates all three contracts
  inputs:
    companies: [Google]
  tasks:
    - id: fetch
      tool: fetch_data
      inputs:
        companies: "{{pipeline.inputs.companies}}"
        year: "{{pipeline.inputs.missing_year}}"
    - id: analyse
      tool: llm_job
      inputs:
        prior: "{{session.undeclared_key}}"
        data: "{{fetch.output}}"
        prompt: "Analyse"
    - id: store_surprise
      tool: store
      inputs:
        key: undeclared_store_key
        value: "{{analyse.output}}"
""")
        sp = make_sub_pipeline(
            reads=[],       # undeclared_key not listed
            stores=[],      # undeclared_store_key not listed
            inputs=["companies"],  # missing_year not in inputs
        )
        violations = validate_contract(pipeline, sp)
        kinds = {v.kind for v in violations}
        assert ViolationKind.MISSING_STORE      not in kinds  # nothing declared, nothing missing
        assert ViolationKind.UNDECLARED_STORE   in kinds
        assert ViolationKind.UNDECLARED_SESSION in kinds
        assert ViolationKind.UNDECLARED_INPUT   in kinds

    def test_violations_list_length_matches_total_issues(self) -> None:
        pipeline = make_pipeline("""
pipeline:
  id: count_violations_pipeline
  goal: count all violations
  tasks:
    - id: fetch
      tool: fetch_data
      inputs:
        source: sec_edgar
    - id: store_a
      tool: store
      inputs:
        key: key_a
        value: "{{fetch.output}}"
    - id: store_b
      tool: store
      inputs:
        key: key_b
        value: "{{fetch.output}}"
""")
        sp = make_sub_pipeline(
            stores=["key_a", "key_c"],  # key_b undeclared, key_c missing → 2 violations
        )
        violations = validate_contract(pipeline, sp)
        assert len(violations) == 2


# ---------------------------------------------------------------------------
# assert_contract() — raising variant
# ---------------------------------------------------------------------------


class TestAssertContract:

    def test_no_violations_does_not_raise(
        self,
        data_acquisition_pipeline: Pipeline,
    ) -> None:
        sp = make_sub_pipeline(
            id="data_acquisition",
            stores=["company_financials", "energy_exposure",
                    "energy_markets", "disruption_context"],
            inputs=["companies", "year"],
        )
        assert_contract(data_acquisition_pipeline, sp)  # should not raise

    def test_raises_contract_error_on_violation(self) -> None:
        pipeline = make_pipeline("""
pipeline:
  id: failing_pipeline
  goal: will fail contract check
  tasks:
    - id: fetch
      tool: fetch_data
      inputs:
        source: sec_edgar
""")
        sp = make_sub_pipeline(stores=["required_key"])
        with pytest.raises(ContractError):
            assert_contract(pipeline, sp)

    def test_error_message_contains_pipeline_id(self) -> None:
        pipeline = make_pipeline("""
pipeline:
  id: identifiable_pipeline
  goal: identifiable in error
  tasks:
    - id: fetch
      tool: fetch_data
      inputs:
        source: sec_edgar
""")
        sp = make_sub_pipeline(id="parent_sp", stores=["missing_key"])
        with pytest.raises(ContractError, match="identifiable_pipeline"):
            assert_contract(pipeline, sp)

    def test_error_message_contains_sub_pipeline_id(self) -> None:
        pipeline = make_pipeline("""
pipeline:
  id: some_pipeline
  goal: test
  tasks:
    - id: fetch
      tool: fetch_data
      inputs:
        source: sec_edgar
""")
        sp = make_sub_pipeline(id="named_sub_pipeline", stores=["missing_key"])
        with pytest.raises(ContractError, match="named_sub_pipeline"):
            assert_contract(pipeline, sp)

    def test_error_message_lists_all_violations(self) -> None:
        pipeline = make_pipeline("""
pipeline:
  id: multi_violation_pipeline
  goal: multiple violations
  tasks:
    - id: fetch
      tool: fetch_data
      inputs:
        source: sec_edgar
    - id: store_extra
      tool: store
      inputs:
        key: extra_key
        value: "{{fetch.output}}"
""")
        sp = make_sub_pipeline(
            stores=["required_key"],  # required_key missing, extra_key undeclared
        )
        with pytest.raises(ContractError) as exc_info:
            assert_contract(pipeline, sp)
        msg = str(exc_info.value)
        assert "missing_store" in msg or "required_key" in msg
        assert "undeclared_store" in msg or "extra_key" in msg

    def test_violation_count_in_error_message(self) -> None:
        pipeline = make_pipeline("""
pipeline:
  id: counted_pipeline
  goal: count violations
  tasks:
    - id: fetch
      tool: fetch_data
      inputs:
        source: sec_edgar
""")
        sp = make_sub_pipeline(stores=["key_a", "key_b", "key_c"])
        with pytest.raises(ContractError, match="3 violation"):
            assert_contract(pipeline, sp)


# ---------------------------------------------------------------------------
# ContractViolation dataclass
# ---------------------------------------------------------------------------


class TestContractViolationDataclass:

    def test_violation_is_hashable(self) -> None:
        v = ContractViolation(
            kind=ViolationKind.MISSING_STORE,
            key="some_key",
            task_id=None,
            message="test message",
        )
        assert hash(v) is not None
        assert {v}  # can be put in a set

    def test_violation_kind_values(self) -> None:
        assert ViolationKind.MISSING_STORE.value      == "missing_store"
        assert ViolationKind.UNDECLARED_STORE.value   == "undeclared_store"
        assert ViolationKind.UNDECLARED_SESSION.value == "undeclared_session"
        assert ViolationKind.UNDECLARED_INPUT.value   == "undeclared_input"