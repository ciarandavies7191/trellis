"""
Models for the Pipeline DSL v1.3 Plan document.

A Plan is the intermediate representation between a complex user goal and the
individual Pipeline YAMLs. It is produced by the model in [PLAN] mode and
consumed by the orchestrator to drive sub-pipeline generation and execution.
"""

from __future__ import annotations

from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator


class SubPipeline(BaseModel):
    """
    A single entry in a Plan's sub_pipelines list.

    Describes one pipeline unit: its goal, what it reads from the session
    blackboard, what it writes back, and which top-level plan inputs it needs.
    The orchestrator uses reads/stores to derive execution order.
    """

    id: str = Field(
        ...,
        description="Unique sub-pipeline identifier, snake_case.",
    )
    goal: str = Field(
        ...,
        description="Natural language goal passed verbatim to the pipeline generator.",
    )
    reads: list[str] = Field(
        default_factory=list,
        description=(
            "Session blackboard keys consumed by this sub-pipeline. "
            "Empty list for root sub-pipelines. Drives topological ordering."
        ),
    )
    stores: list[str] = Field(
        default_factory=list,
        description=(
            "Session blackboard keys produced by this sub-pipeline. "
            "Must match `store` task keys in the generated pipeline."
        ),
    )
    inputs: list[str] = Field(
        default_factory=list,
        description="Plan-level input keys forwarded to this sub-pipeline.",
    )

    @field_validator("id")
    @classmethod
    def id_must_be_snake_case(cls, v: str) -> str:
        if (
            not v
            or not v[0].isalpha()
            or not v.replace("_", "").isalnum()
            or v != v.lower()
        ):
            raise ValueError(
                f"Sub-pipeline id must be snake_case alphanumeric starting with a letter, got: {v!r}"
            )
        return v

    @field_validator("reads", "stores", "inputs", mode="before")
    @classmethod
    def coerce_none_to_list(cls, v: Any) -> list:
        """YAML nulls on list fields arrive as None; normalise to empty list."""
        return v if v is not None else []


class Plan(BaseModel):
    """
    Top-level Plan document (produced by the model in [PLAN] mode).

    Wraps the `plan:` root key of the DSL YAML.
    """

    id: str = Field(
        ...,
        description="Unique plan identifier, snake_case.",
    )
    goal: str = Field(
        ...,
        description="Natural language restatement of the overall intent.",
    )
    inputs: dict[str, Any] = Field(
        default_factory=dict,
        description="Optional top-level parameters passed into the plan.",
    )
    sub_pipelines: list[SubPipeline] = Field(
        ...,
        min_length=1,
        description="Ordered or unordered list of sub-pipeline entries.",
    )

    @field_validator("id")
    @classmethod
    def id_must_be_snake_case(cls, v: str) -> str:
        if (
            not v
            or not v[0].isalpha()
            or not v.replace("_", "").isalnum()
            or v != v.lower()
        ):
            raise ValueError(
                f"Plan id must be snake_case alphanumeric starting with a letter, got: {v!r}"
            )
        return v

    @field_validator("inputs", mode="before")
    @classmethod
    def coerce_none_to_dict(cls, v: Any) -> dict:
        return v if v is not None else {}

    @model_validator(mode="after")
    def sub_pipeline_ids_are_unique(self) -> Plan:
        ids = [sp.id for sp in self.sub_pipelines]
        duplicates = {i for i in ids if ids.count(i) > 1}
        if duplicates:
            raise ValueError(
                f"Duplicate sub-pipeline ids in plan {self.id!r}: {duplicates}"
            )
        return self

    @model_validator(mode="after")
    def inputs_keys_referenced_exist(self) -> Plan:
        """
        Every key listed in a sub-pipeline's `inputs` must exist in plan.inputs.
        Catches forward-reference errors at parse time.
        """
        plan_input_keys = set(self.inputs.keys())
        for sp in self.sub_pipelines:
            unknown = set(sp.inputs) - plan_input_keys
            if unknown:
                raise ValueError(
                    f"Sub-pipeline {sp.id!r} references unknown plan input keys: "
                    f"{unknown}. Available: {plan_input_keys}"
                )
        return self

    # ------------------------------------------------------------------
    # Construction helpers
    # ------------------------------------------------------------------

    @classmethod
    def from_yaml(cls, text: str) -> Plan:
        """
        Parse and validate a Plan from a YAML string.

        The YAML must have a top-level `plan:` key, matching the DSL spec.

        Raises:
            ValueError: if the `plan:` root key is missing.
            pydantic.ValidationError: if the document is structurally invalid.
        """
        doc = yaml.safe_load(text)
        if not isinstance(doc, dict) or "plan" not in doc:
            raise ValueError(
                "Plan YAML must have a top-level `plan:` key. "
                f"Got top-level keys: {list(doc.keys()) if isinstance(doc, dict) else type(doc)}"
            )
        return cls.model_validate(doc["plan"])

    @classmethod
    def from_yaml_file(cls, path: str) -> Plan:
        """Load and validate a Plan from a YAML file on disk."""
        with open(path, "r", encoding="utf-8") as fh:
            return cls.from_yaml(fh.read())