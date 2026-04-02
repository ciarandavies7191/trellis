"""
Models for the Pipeline DSL v1.3 Pipeline document.

A Pipeline is the executable unit — a DAG of tasks produced by the model in
[PIPELINE] mode. Each sub-pipeline entry in a Plan produces one Pipeline
document when the generator runs.
"""

from __future__ import annotations

import re
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# Template handling
# ---------------------------------------------------------------------------

#: Matches any {{...}} expression anywhere in a string value.
_TEMPLATE_RE = re.compile(r"\{\{([^}]+)\}\}")

#: Valid tool names as defined in DSL v1.3 Tool Registry.
KNOWN_TOOLS: frozenset[str] = frozenset(
    {
        "load_document",
        "select",
        "extract_table",
        "extract_text",
        "llm_job",
        "fetch_data",
        "search_web",
        "store",
        "export",
    }
)


def extract_template_refs(value: Any) -> list[str]:
    """
    Recursively walk a task input value and return every {{expr}} expression
    found, as a flat list of raw expression strings (without braces).

    Handles str, list, and dict values — mirrors the polymorphic input model.
    """
    refs: list[str] = []
    if isinstance(value, str):
        refs.extend(m.group(1).strip() for m in _TEMPLATE_RE.finditer(value))
    elif isinstance(value, list):
        for item in value:
            refs.extend(extract_template_refs(item))
    elif isinstance(value, dict):
        for v in value.values():
            refs.extend(extract_template_refs(v))
    return refs


# ---------------------------------------------------------------------------
# Task model
# ---------------------------------------------------------------------------


class Task(BaseModel):
    """
    A single task node in a Pipeline DAG.

    Dependencies are implicit — derived from {{task_id.output}} references in
    `inputs` and `parallel_over` by the DAG executor. There is no `depends_on`
    field.
    """

    id: str = Field(
        ...,
        description="Unique task identifier within the pipeline, snake_case.",
    )
    tool: str = Field(
        ...,
        description="Name of the tool to invoke (must be registered at runtime).",
    )
    inputs: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Key-value pairs passed to the tool. "
            "Values may be literals or {{template}} strings."
        ),
    )
    parallel_over: str | None = Field(
        default=None,
        description=(
            "Fan-out: runs the task once per item in the referenced list. "
            "Must be a {{template}} string. Item is bound to {{item}}."
        ),
    )
    retry: int = Field(
        default=0,
        ge=0,
        description="Number of retry attempts on task failure.",
    )
    await_: list[str] = Field(
        default_factory=list,
        alias="await",
        description=(
            "Explicit barrier — wait for listed task IDs without consuming output. "
            "Use only when no input reference exists."
        ),
    )

    model_config = {"populate_by_name": True}

    @field_validator("id")
    @classmethod
    def id_must_be_snake_case(cls, v: str) -> str:
        if (
            not v
            or not v[0].isalpha()
            or not v.replace("_", "").isalnum()
            or v != v.lower()
        ):
            raise ValueError(f"Task id must be snake_case alphanumeric starting with a letter, got: {v!r}")
        return v

    @field_validator("tool")
    @classmethod
    def tool_must_be_known(cls, v: str) -> str:
        if v not in KNOWN_TOOLS:
            raise ValueError(
                f"Unknown tool {v!r}. Known tools: {sorted(KNOWN_TOOLS)}"
            )
        return v

    @field_validator("parallel_over")
    @classmethod
    def parallel_over_must_be_template(cls, v: str | None) -> str | None:
        if v is not None and not _TEMPLATE_RE.search(v):
            raise ValueError(
                f"`parallel_over` must contain a {{{{template}}}} expression, got: {v!r}"
            )
        return v

    @field_validator("inputs", mode="before")
    @classmethod
    def coerce_none_to_dict(cls, v: Any) -> dict:
        return v if v is not None else {}

    @field_validator("await_", mode="before")
    @classmethod
    def coerce_await_none_to_list(cls, v: Any) -> list:
        return v if v is not None else []

    # ------------------------------------------------------------------
    # Derived helpers (used by the executor and validator)
    # ------------------------------------------------------------------

    def template_refs(self) -> list[str]:
        """
        Return all {{expr}} expressions referenced in this task's inputs and
        parallel_over field. Used by the DAG builder to infer dependencies.
        """
        refs = extract_template_refs(self.inputs)
        if self.parallel_over:
            refs.extend(extract_template_refs(self.parallel_over))
        return refs

    def upstream_task_ids(self) -> set[str]:
        """
        Parse template refs and return the set of task IDs this task depends on.

        Recognises:
          - {{task_id.output}}          → task_id
          - {{task_id.output.field}}    → task_id
          - {{pipeline.inputs.*}}       → (pipeline input, no task dependency)
          - {{pipeline.goal}}           → (pipeline metadata, no task dependency)
          - {{session.*}}               → (blackboard, no task dependency)
          - {{item}}                    → (fan-out binding, no task dependency)

        Explicit `await` ids are also included.
        """
        ids: set[str] = set()
        for expr in self.template_refs():
            parts = expr.split(".")
            # Exclude known non-task namespaces
            if parts[0] in ("pipeline", "session", "item"):
                continue
            # Everything else is treated as a task id reference
            ids.add(parts[0])
        ids.update(self.await_)
        # A task cannot depend on itself (validated separately at pipeline level)
        ids.discard(self.id)
        return ids


# ---------------------------------------------------------------------------
# Pipeline model
# ---------------------------------------------------------------------------


class Pipeline(BaseModel):
    """
    Top-level Pipeline document (produced by the model in [PIPELINE] mode).

    Wraps the `pipeline:` root key of the DSL YAML.
    """

    id: str = Field(
        ...,
        description="Unique pipeline identifier, snake_case. Should match plan sub-pipeline id.",
    )
    goal: str = Field(
        ...,
        description="Human-readable description of this pipeline's intent.",
    )
    inputs: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Named input parameters provided by the orchestrator at execution time. "
            "Referenced as {{pipeline.inputs.key}}."
        ),
    )
    tasks: list[Task] = Field(
        ...,
        min_length=1,
        description="List of task objects forming the DAG.",
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
            raise ValueError(f"Pipeline id must be snake_case alphanumeric starting with a letter, got: {v!r}")
        return v

    @field_validator("inputs", mode="before")
    @classmethod
    def coerce_none_to_dict(cls, v: Any) -> dict:
        return v if v is not None else {}

    @model_validator(mode="after")
    def task_ids_are_unique(self) -> Pipeline:
        ids = [t.id for t in self.tasks]
        duplicates = {i for i in ids if ids.count(i) > 1}
        if duplicates:
            raise ValueError(
                f"Duplicate task ids in pipeline {self.id!r}: {duplicates}"
            )
        return self

    @model_validator(mode="after")
    def await_ids_exist(self) -> Pipeline:
        """Every id in a task's `await` list must refer to another task in this pipeline."""
        task_ids = {t.id for t in self.tasks}
        for task in self.tasks:
            unknown = set(task.await_) - task_ids
            if unknown:
                raise ValueError(
                    f"Task {task.id!r} awaits unknown task ids: {unknown}"
                )
        return self

    @model_validator(mode="after")
    def upstream_refs_exist(self) -> Pipeline:
        """
        Every task-id reference in {{task_id.output}} templates must resolve to
        a task that exists in this pipeline.

        Session and pipeline.inputs refs are not checked here — those are
        validated by the contract validator against the plan's reads list.
        """
        task_ids = {t.id for t in self.tasks}
        for task in self.tasks:
            unknown = task.upstream_task_ids() - task_ids - set(task.await_)
            # Re-check: upstream_task_ids already excludes pipeline/session/item
            unresolved = task.upstream_task_ids() - task_ids
            if unresolved:
                raise ValueError(
                    f"Task {task.id!r} references unknown task ids: {unresolved}"
                )
        return self

    # ------------------------------------------------------------------
    # Derived helpers
    # ------------------------------------------------------------------

    def task_map(self) -> dict[str, Task]:
        """Return a {task_id: Task} mapping for O(1) lookups."""
        return {t.id: t for t in self.tasks}

    def store_keys(self) -> list[str]:
        """Return all blackboard keys written by `store` tasks in this pipeline."""
        return [
            t.inputs["key"]
            for t in self.tasks
            if t.tool == "store" and "key" in t.inputs
        ]

    # ------------------------------------------------------------------
    # Construction helpers
    # ------------------------------------------------------------------

    @classmethod
    def from_yaml(cls, text: str) -> Pipeline:
        """
        Parse and validate a Pipeline from a YAML string.

        The YAML must have a top-level `pipeline:` key, matching the DSL spec.

        Raises:
            ValueError: if the `pipeline:` root key is missing.
            pydantic.ValidationError: if the document is structurally invalid.
        """
        doc = yaml.safe_load(text)
        if not isinstance(doc, dict) or "pipeline" not in doc:
            raise ValueError(
                "Pipeline YAML must have a top-level `pipeline:` key. "
                f"Got top-level keys: {list(doc.keys()) if isinstance(doc, dict) else type(doc)}"
            )
        return cls.model_validate(doc["pipeline"])

    @classmethod
    def from_yaml_file(cls, path: str) -> Pipeline:
        """Load and validate a Pipeline from a YAML file on disk."""
        with open(path, "r", encoding="utf-8") as fh:
            return cls.from_yaml(fh.read())