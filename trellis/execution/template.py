"""
trellis.execution.template — Template resolver for Pipeline DSL v1.3.

Resolves {{expr}} template expressions in task input values against a
runtime context carrying task outputs, pipeline inputs, session blackboard
state, and the current {{item}} binding for parallel_over fan-outs.

Public API
----------
    ResolutionContext        — dataclass holding all five resolvable namespaces
    resolve()                — resolve a single value (str, list, dict, or literal)
    resolve_inputs()         — resolve an entire task inputs dict
    resolve_parallel_over()  — resolve a parallel_over expression to a list

Template forms supported
------------------------
    {{task_id.output}}              full output of a completed task
    {{task_id.output.field}}        named field within a task's output
    {{pipeline.inputs.key}}         a named pipeline input parameter
    {{pipeline.goal}}               the pipeline goal string
    {{session.key}}                 a value from the session blackboard
    {{item}}                        current element in a parallel_over loop

Resolution rules
----------------
    Whole-value template:  "{{expr}}"          -> returns resolved value as-is
                                                  (preserves type: list, dict, etc.)
    Embedded template:     "prefix {{expr}} ..." -> string interpolation,
                                                    always returns str
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from trellis.exceptions import ResolutionError

# ---------------------------------------------------------------------------
# Template regex
# ---------------------------------------------------------------------------

#: Matches a single {{expr}} anywhere in a string.
_TEMPLATE_RE = re.compile(r"\{\{\s*([^}]+?)\s*\}\}")

#: Matches a string that is *entirely* one template expression and nothing else.
_WHOLE_VALUE_RE = re.compile(r"^\{\{\s*([^}]+?)\s*\}\}$")


# ---------------------------------------------------------------------------
# Resolution context
# ---------------------------------------------------------------------------


@dataclass
class ResolutionContext:
    """
    All state available to the template resolver during pipeline execution.

    Attributes:
        task_outputs:    Mapping of task_id -> output value for every task
                         that has completed in this pipeline execution.
        pipeline_inputs: The pipeline's inputs block (key -> value).
        pipeline_goal:   The pipeline's goal string.
        session:         The session blackboard (key -> value), pre-filtered
                         to only the keys declared in the sub-pipeline's reads.
        item:            The current element binding for a parallel_over task.
                         None when not inside a fan-out execution.
    """
    task_outputs:    dict[str, Any] = field(default_factory=dict)
    pipeline_inputs: dict[str, Any] = field(default_factory=dict)
    pipeline_goal:   str            = ""
    session:         dict[str, Any] = field(default_factory=dict)
    item:            Any            = None

    def set_task_output(self, task_id: str, output: Any) -> None:
        """Record a completed task's output in the context."""
        self.task_outputs[task_id] = output

    def with_item(self, item: Any) -> ResolutionContext:
        """Return a shallow copy of this context with {{item}} bound to item."""
        import copy
        ctx = copy.copy(self)
        ctx.item = item
        return ctx


# ---------------------------------------------------------------------------
# Field path walking
# ---------------------------------------------------------------------------


def _walk_path(value: Any, path: list[str], full_expr: str) -> Any:
    """
    Traverse a sequence of field names into a resolved value.

    Tries dict-key lookup first, then attribute access. Raises ResolutionError
    if any segment cannot be resolved.
    """
    for segment in path:
        if isinstance(value, dict):
            if segment not in value:
                raise ResolutionError(
                    f"Cannot resolve {full_expr!r}: key {segment!r} not found "
                    f"in dict with keys {sorted(str(k) for k in value.keys())}."
                )
            value = value[segment]
        elif hasattr(value, segment):
            value = getattr(value, segment)
        else:
            raise ResolutionError(
                f"Cannot resolve {full_expr!r}: {type(value).__name__!r} object "
                f"has no attribute or key {segment!r}."
            )
    return value


# ---------------------------------------------------------------------------
# Expression resolver
# ---------------------------------------------------------------------------


def _resolve_expr(expr: str, ctx: ResolutionContext) -> Any:
    """
    Resolve a single template expression string (without braces) to a value.
    """
    parts = expr.split(".")

    # {{item}} ---------------------------------------------------------------
    if parts[0] == "item":
        if ctx.item is None:
            raise ResolutionError(
                "Cannot resolve {{item}}: not inside a parallel_over execution."
            )
        root = ctx.item
        return _walk_path(root, parts[1:], expr) if len(parts) > 1 else root

    # {{pipeline.inputs.key}} / {{pipeline.goal}} ----------------------------
    if parts[0] == "pipeline":
        if len(parts) < 2:
            raise ResolutionError(
                f"Cannot resolve {{{{{expr}}}}}: "
                f"'pipeline' namespace requires at least one sub-key."
            )
        if parts[1] == "goal":
            return ctx.pipeline_goal
        if parts[1] == "inputs":
            if len(parts) < 3:
                raise ResolutionError(
                    f"Cannot resolve {{{{{expr}}}}}: "
                    f"'pipeline.inputs' requires a key name."
                )
            key = parts[2]
            if key not in ctx.pipeline_inputs:
                raise ResolutionError(
                    f"Cannot resolve {{{{{expr}}}}}: pipeline input {key!r} not found. "
                    f"Available inputs: {sorted(ctx.pipeline_inputs.keys()) or []}."
                )
            root = ctx.pipeline_inputs[key]
            return _walk_path(root, parts[3:], expr) if len(parts) > 3 else root
        raise ResolutionError(
            f"Cannot resolve {{{{{expr}}}}}: unknown pipeline sub-key {parts[1]!r}. "
            f"Supported: 'pipeline.inputs.*', 'pipeline.goal'."
        )

    # {{session.key}} --------------------------------------------------------
    if parts[0] == "session":
        if len(parts) < 2:
            raise ResolutionError(
                f"Cannot resolve {{{{{expr}}}}}: 'session' requires a key name."
            )
        key = parts[1]
        if key not in ctx.session:
            raise ResolutionError(
                f"Cannot resolve {{{{{expr}}}}}: session key {key!r} not found. "
                f"Available session keys: {sorted(ctx.session.keys()) or []}."
            )
        root = ctx.session[key]
        return _walk_path(root, parts[2:], expr) if len(parts) > 2 else root

    # {{task_id.output}} / {{task_id.output.field}} --------------------------
    task_id = parts[0]
    if task_id not in ctx.task_outputs:
        raise ResolutionError(
            f"Cannot resolve {{{{{expr}}}}}: task {task_id!r} has no output "
            f"in the current context. "
            f"Available tasks: {sorted(ctx.task_outputs.keys()) or []}."
        )
    root = ctx.task_outputs[task_id]
    remaining = parts[1:]
    if remaining and remaining[0] == "output":
        root = _walk_path(root, remaining[1:], expr)
    elif remaining:
        root = _walk_path(root, remaining, expr)
    return root


# ---------------------------------------------------------------------------
# Public resolve functions
# ---------------------------------------------------------------------------


def resolve(value: Any, ctx: ResolutionContext) -> Any:
    """
    Resolve all {{expr}} template expressions in a value.

    Recursively handles str, list, and dict values. Literals (int, float,
    bool, None) are returned unchanged.

    Whole-value rule: if value is a string consisting of exactly one {{expr}}
    and nothing else, the resolved Python object is returned directly
    (preserving its type). This allows structured outputs (lists, dicts) to
    flow between tasks without being stringified.

    Interpolation rule: if value contains one or more {{expr}} mixed with
    surrounding text, all expressions are stringified and interpolated.
    """
    if isinstance(value, str):
        whole = _WHOLE_VALUE_RE.match(value)
        if whole:
            return _resolve_expr(whole.group(1).strip(), ctx)

        def _replace(m: re.Match) -> str:
            return str(_resolve_expr(m.group(1).strip(), ctx))

        return _TEMPLATE_RE.sub(_replace, value)

    if isinstance(value, list):
        return [resolve(item, ctx) for item in value]

    if isinstance(value, dict):
        return {k: resolve(v, ctx) for k, v in value.items()}

    return value


def resolve_inputs(inputs: dict[str, Any], ctx: ResolutionContext) -> dict[str, Any]:
    """
    Resolve all template expressions in a task's inputs dict.

    Convenience wrapper around resolve() for the common case of resolving
    an entire task inputs block before invoking the tool.
    """
    return {key: resolve(value, ctx) for key, value in inputs.items()}


def resolve_parallel_over(parallel_over: str, ctx: ResolutionContext) -> list[Any]:
    """
    Resolve a parallel_over expression to a list of items for fan-out.

    The expression must resolve to a non-string iterable. Raises
    ResolutionError if the result is a string or not iterable.
    """
    result = resolve(parallel_over, ctx)

    if isinstance(result, str):
        raise ResolutionError(
            f"parallel_over {parallel_over!r} resolved to a string {result!r}. "
            f"Expected a list."
        )
    try:
        return list(result)
    except TypeError:
        raise ResolutionError(
            f"parallel_over {parallel_over!r} resolved to "
            f"{type(result).__name__!r} which is not iterable. Expected a list."
        )