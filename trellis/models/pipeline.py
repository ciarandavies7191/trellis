"""
Models for the Pipeline DSL v1.4 Pipeline document.

A Pipeline is the executable unit — a DAG of tasks produced by the model in
[PIPELINE] mode. Each sub-pipeline entry in a Plan produces one Pipeline
document when the generator runs.
"""

from __future__ import annotations

import re
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# Template handling
# ---------------------------------------------------------------------------

#: Matches any {{...}} expression anywhere in a string value.
_TEMPLATE_RE = re.compile(r"\{\{([^}]+)\}\}")

#: Sentinel used to distinguish "no default provided" from an explicit `default: null`.
_MISSING: object = object()

#: Recognised param type names.
VALID_PARAM_TYPES: frozenset[str] = frozenset(
    {"string", "integer", "number", "boolean", "list", "object"}
)

#: Valid tool names as defined in DSL v1.4 Tool Registry.
KNOWN_TOOLS: frozenset[str] = frozenset(
    {
        "ingest_document",
        "load_schema",        # NEW in v1.4
        "select",
        "extract_from_texts",
        "extract_from_tables",
        "extract_chart",
        "extract_fields",     # NEW in v1.4
        "llm_job",
        "fetch_data",
        "search_web",
        "compute",            # NEW in v1.4
        "store",
        "export",
        "mock",
        "failing_mock",
        "flaky_tool",
        "reliable_tool",
        "permanent_failure",
        "tracker",
        "tracker_1",
        "tracker_2",
        "tracker_3",
        "tracker_4",
        "classify_page",
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
# Pipeline parameter model
# ---------------------------------------------------------------------------


class PipelineParam(BaseModel):
    """
    Declaration of a single pipeline-level parameter.

    Parameters are referenced in task inputs, parallel_over, and the goal
    string via the ``{{params.name}}`` template syntax. They are resolved
    before any task executes, binding a concrete value for every run.

    Attributes:
        type:        Expected value type. Used for coercion and validation at
                     invocation time. One of: string, integer, number, boolean,
                     list, object.
        description: Human-readable description of the parameter's purpose.
        default:     Default value used when the caller does not supply the
                     param. Absence of this field means the param is required.
    """

    type: str = Field(default="string", description="Value type for coercion.")
    description: str = Field(default="", description="Human-readable description.")
    default: Any = Field(default=_MISSING, description="Default value; absent = required.")

    @field_validator("type")
    @classmethod
    def type_must_be_valid(cls, v: str) -> str:
        if v not in VALID_PARAM_TYPES:
            raise ValueError(
                f"Invalid param type {v!r}. Valid types: {sorted(VALID_PARAM_TYPES)}"
            )
        return v

    @property
    def required(self) -> bool:
        """True when no default is declared — the caller must supply a value."""
        return self.default is _MISSING

    def coerce(self, value: Any, name: str) -> Any:
        """
        Coerce *value* to this param's declared type.

        Raises:
            ValueError: if the value cannot be coerced.
        """
        try:
            if self.type == "string":
                return str(value)
            if self.type == "integer":
                return int(value)
            if self.type == "number":
                return float(value)
            if self.type == "boolean":
                if isinstance(value, bool):
                    return value
                if isinstance(value, str):
                    return value.lower() in ("true", "1", "yes")
                return bool(value)
            if self.type == "list":
                if not isinstance(value, list):
                    raise ValueError("expected a list")
                return value
            if self.type == "object":
                if not isinstance(value, dict):
                    raise ValueError("expected an object (dict)")
                return value
        except (ValueError, TypeError) as exc:
            raise ValueError(
                f"Param {name!r}: cannot coerce {value!r} to type {self.type!r}: {exc}"
            ) from exc
        return value  # unreachable but satisfies type checkers


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

    @model_validator(mode="after")
    def item_ref_requires_parallel_over(self) -> "Task":
        """{{item}} in inputs is only valid when parallel_over is set."""
        refs = extract_template_refs(self.inputs)
        uses_item = any(r.strip().split(".")[0] == "item" for r in refs)
        if uses_item and self.parallel_over is None:
            raise ValueError(
                f"Task {self.id!r} references {{{{item}}}} in inputs but has no `parallel_over`. "
                "`{{item}}` is only bound during fan-out execution."
            )
        return self

    @model_validator(mode="after")
    def parallel_over_requires_item_ref(self) -> "Task":
        """A task with parallel_over must reference {{item}} somewhere in its inputs."""
        if self.parallel_over is None:
            return self
        refs = extract_template_refs(self.inputs)
        uses_item = any(r.strip().split(".")[0] == "item" for r in refs)
        if not uses_item:
            raise ValueError(
                f"Task {self.id!r} declares `parallel_over` but never references {{{{item}}}} "
                "in its inputs — the fan-out binding is unused."
            )
        return self

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

        Recognizes:
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
            if parts[0] in ("pipeline", "session", "item", "params"):
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
    params: dict[str, PipelineParam] = Field(
        default_factory=dict,
        description=(
            "Typed, named pipeline parameters. Referenced as {{params.key}} in "
            "the goal, task inputs, and parallel_over. Resolved before any task runs."
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

    @field_validator("params", mode="before")
    @classmethod
    def coerce_params_none_to_dict(cls, v: Any) -> dict:
        return v if v is not None else {}

    @model_validator(mode="after")
    def params_refs_exist(self) -> "Pipeline":
        """All {{params.key}} references in the goal and task inputs must be declared."""
        declared = set(self.params.keys())

        # Scan goal string
        for ref in extract_template_refs(self.goal):
            parts = ref.split(".")
            if parts[0] == "params":
                if len(parts) < 2:
                    raise ValueError(
                        "{{params}} used in goal without a key name — use {{params.key}}."
                    )
                key = parts[1]
                if key not in declared:
                    raise ValueError(
                        f"Goal references undeclared param {{{{params.{key}}}}}. "
                        f"Declared params: {sorted(declared) or '[]'}."
                    )

        # Scan task inputs and parallel_over
        for task in self.tasks:
            all_refs = extract_template_refs(task.inputs)
            if task.parallel_over:
                all_refs.extend(extract_template_refs(task.parallel_over))
            for ref in all_refs:
                parts = ref.split(".")
                if parts[0] == "params":
                    if len(parts) < 2:
                        raise ValueError(
                            f"Task {task.id!r}: {{{{params}}}} used without a key name — "
                            "use {{params.key}}."
                        )
                    key = parts[1]
                    if key not in declared:
                        raise ValueError(
                            f"Task {task.id!r} references undeclared param "
                            f"{{{{params.{key}}}}}. "
                            f"Declared params: {sorted(declared) or '[]'}."
                        )
        return self

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
    def compute_tasks_have_function(self) -> Pipeline:
        """Every `compute` task must declare a `function` input key."""
        for task in self.tasks:
            if task.tool == "compute" and "function" not in task.inputs:
                raise ValueError(
                    f"Task {task.id!r}: tool 'compute' requires a 'function' input key."
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
        """Return all literal blackboard keys written by `store` tasks in this pipeline.

        Keys that are themselves template strings (e.g. ``{{pipeline.inputs.key}}``)
        are excluded — they can only be resolved at runtime and must not be compared
        against the static contract declared in the plan.
        """
        return [
            t.inputs["key"]
            for t in self.tasks
            if t.tool == "store"
            and "key" in t.inputs
            and isinstance(t.inputs["key"], str)
            and not _TEMPLATE_RE.search(t.inputs["key"])
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