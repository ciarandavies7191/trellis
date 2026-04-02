"""
trellis.validation.contract — Pipeline/SubPipeline contract validation.

Validates that a generated Pipeline honours the contract declared by its
parent SubPipeline entry in the Plan:

  1. Stores contract   — pipeline store tasks write exactly the keys in
                         sub_pipeline.stores (no missing, no undeclared extras)

  2. Reads contract    — every {{session.key}} reference in the pipeline
                         resolves to a key listed in sub_pipeline.reads

  3. Inputs contract   — every {{pipeline.inputs.key}} reference resolves
                         to a key declared in pipeline.inputs

All violations are collected before raising so callers see the full picture
in a single ContractError.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

from trellis.exceptions import ContractError
from trellis.models.pipeline import Pipeline, extract_template_refs
from trellis.models.plan import SubPipeline


# ---------------------------------------------------------------------------
# Violation model
# ---------------------------------------------------------------------------


class ViolationKind(str, Enum):
    MISSING_STORE       = "missing_store"        # declared in stores, not written
    UNDECLARED_STORE    = "undeclared_store"      # written but not declared in stores
    UNDECLARED_SESSION  = "undeclared_session"    # {{session.key}} not in reads
    UNDECLARED_INPUT    = "undeclared_input"      # {{pipeline.inputs.key}} not in pipeline.inputs


@dataclass(frozen=True)
class ContractViolation:
    """
    A single contract violation found during validation.

    Attributes:
        kind:     Category of violation.
        key:      The blackboard key or input key involved.
        task_id:  The task where the violation was found (None for store-level checks).
        message:  Human-readable description.
    """
    kind:    ViolationKind
    key:     str
    task_id: str | None
    message: str


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_SESSION_RE   = re.compile(r"\{\{\s*session\.([^}]+?)\s*\}\}")
_INPUT_RE     = re.compile(r"\{\{\s*pipeline\.inputs\.([^}]+?)\s*\}\}")


def _extract_session_keys_from_value(value: object) -> list[str]:
    """Return all session key names referenced in a template value (recursive)."""
    keys: list[str] = []
    if isinstance(value, str):
        keys.extend(m.group(1).strip() for m in _SESSION_RE.finditer(value))
    elif isinstance(value, list):
        for item in value:
            keys.extend(_extract_session_keys_from_value(item))
    elif isinstance(value, dict):
        for v in value.values():
            keys.extend(_extract_session_keys_from_value(v))
    return keys


def _extract_input_keys_from_value(value: object) -> list[str]:
    """Return all pipeline.inputs key names referenced in a template value (recursive)."""
    keys: list[str] = []
    if isinstance(value, str):
        keys.extend(m.group(1).strip() for m in _INPUT_RE.finditer(value))
    elif isinstance(value, list):
        for item in value:
            keys.extend(_extract_input_keys_from_value(item))
    elif isinstance(value, dict):
        for v in value.values():
            keys.extend(_extract_input_keys_from_value(v))
    return keys


def _pipeline_session_refs(pipeline: Pipeline) -> dict[str, list[str]]:
    """
    Return a mapping of session key → [task_ids that reference it].
    Scans all task inputs and parallel_over fields.
    """
    refs: dict[str, list[str]] = {}
    for task in pipeline.tasks:
        values_to_scan = list(task.inputs.values())
        if task.parallel_over:
            values_to_scan.append(task.parallel_over)
        for value in values_to_scan:
            for key in _extract_session_keys_from_value(value):
                refs.setdefault(key, []).append(task.id)
    return refs


def _pipeline_input_refs(pipeline: Pipeline) -> dict[str, list[str]]:
    """
    Return a mapping of pipeline.inputs key → [task_ids that reference it].
    Scans all task inputs and parallel_over fields.
    """
    refs: dict[str, list[str]] = {}
    for task in pipeline.tasks:
        values_to_scan = list(task.inputs.values())
        if task.parallel_over:
            values_to_scan.append(task.parallel_over)
        for value in values_to_scan:
            for key in _extract_input_keys_from_value(value):
                refs.setdefault(key, []).append(task.id)
    return refs


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def _check_stores(
    pipeline: Pipeline,
    sub_pipeline: SubPipeline,
) -> list[ContractViolation]:
    """
    Verify the pipeline's store tasks match the sub-pipeline's stores declaration.

    Violations:
      - MISSING_STORE:    key in sub_pipeline.stores but no store task writes it
      - UNDECLARED_STORE: store task writes a key not in sub_pipeline.stores
    """
    violations: list[ContractViolation] = []

    declared  = set(sub_pipeline.stores)
    written   = set(pipeline.store_keys())

    for key in sorted(declared - written):
        violations.append(ContractViolation(
            kind    = ViolationKind.MISSING_STORE,
            key     = key,
            task_id = None,
            message = (
                f"Sub-pipeline {sub_pipeline.id!r} declares {key!r} in `stores` "
                f"but no `store` task in the pipeline writes this key."
            ),
        ))

    for key in sorted(written - declared):
        # Find the task that writes it for a helpful error message
        task_id = next(
            (t.id for t in pipeline.tasks
             if t.tool == "store" and t.inputs.get("key") == key),
            None,
        )
        violations.append(ContractViolation(
            kind    = ViolationKind.UNDECLARED_STORE,
            key     = key,
            task_id = task_id,
            message = (
                f"Task {task_id!r} writes blackboard key {key!r} but this key "
                f"is not declared in sub-pipeline {sub_pipeline.id!r} `stores`. "
                f"Declared stores: {sorted(declared) or '[]'}."
            ),
        ))

    return violations


def _check_reads(
    pipeline: Pipeline,
    sub_pipeline: SubPipeline,
) -> list[ContractViolation]:
    """
    Verify all {{session.key}} references are declared in sub_pipeline.reads.

    Violations:
      - UNDECLARED_SESSION: session key used but not in sub_pipeline.reads
    """
    violations: list[ContractViolation] = []

    declared_reads = set(sub_pipeline.reads)
    session_refs   = _pipeline_session_refs(pipeline)

    for key in sorted(session_refs):
        if key not in declared_reads:
            task_ids = session_refs[key]
            violations.append(ContractViolation(
                kind    = ViolationKind.UNDECLARED_SESSION,
                key     = key,
                task_id = task_ids[0],  # first task that references it
                message = (
                    f"Task(s) {task_ids} reference {{{{session.{key}}}}} but "
                    f"{key!r} is not declared in sub-pipeline "
                    f"{sub_pipeline.id!r} `reads`. "
                    f"Declared reads: {sorted(declared_reads) or '[]'}."
                ),
            ))

    return violations


def _check_inputs(pipeline: Pipeline) -> list[ContractViolation]:
    """
    Verify all {{pipeline.inputs.key}} references resolve to declared inputs.

    Violations:
      - UNDECLARED_INPUT: key referenced in template but not in pipeline.inputs
    """
    violations: list[ContractViolation] = []

    declared_inputs = set(pipeline.inputs.keys())
    input_refs      = _pipeline_input_refs(pipeline)

    for key in sorted(input_refs):
        if key not in declared_inputs:
            task_ids = input_refs[key]
            violations.append(ContractViolation(
                kind    = ViolationKind.UNDECLARED_INPUT,
                key     = key,
                task_id = task_ids[0],
                message = (
                    f"Task(s) {task_ids} reference {{{{pipeline.inputs.{key}}}}} "
                    f"but {key!r} is not declared in pipeline.inputs. "
                    f"Declared inputs: {sorted(declared_inputs) or '[]'}."
                ),
            ))

    return violations


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def validate_contract(
    pipeline: Pipeline,
    sub_pipeline: SubPipeline,
) -> list[ContractViolation]:
    """
    Validate a Pipeline against its parent SubPipeline contract.

    Runs all three checks (stores, reads, inputs) and returns every violation
    found. Returns an empty list if the pipeline fully honours its contract.

    Args:
        pipeline:     A structurally valid Pipeline model instance.
        sub_pipeline: The SubPipeline entry from the Plan that generated it.

    Returns:
        A (possibly empty) list of ContractViolation objects.
    """
    violations: list[ContractViolation] = []
    violations.extend(_check_stores(pipeline, sub_pipeline))
    violations.extend(_check_reads(pipeline, sub_pipeline))
    violations.extend(_check_inputs(pipeline))
    return violations


def assert_contract(
    pipeline: Pipeline,
    sub_pipeline: SubPipeline,
) -> None:
    """
    Validate a Pipeline against its parent SubPipeline contract and raise
    if any violations are found.

    This is the raising variant of validate_contract() — use it in the
    validation pipeline where a hard failure is appropriate.

    Args:
        pipeline:     A structurally valid Pipeline model instance.
        sub_pipeline: The SubPipeline entry from the Plan that generated it.

    Raises:
        ContractError: if any violations are found, with a summary of all of them.
    """
    violations = validate_contract(pipeline, sub_pipeline)
    if not violations:
        return

    lines = [
        f"Pipeline {pipeline.id!r} violates its contract with "
        f"sub-pipeline {sub_pipeline.id!r} "
        f"({len(violations)} violation(s)):"
    ]
    for i, v in enumerate(violations, 1):
        prefix = f"  {i}. [{v.kind.value}]"
        suffix = f" (task: {v.task_id!r})" if v.task_id else ""
        lines.append(f"{prefix}{suffix} {v.message}")

    raise ContractError("\n".join(lines))