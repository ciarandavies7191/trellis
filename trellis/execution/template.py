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
    {{task_id.output.list.first}}   first element of a list in a task's output
    {{task_id.output.list.last}}    last element of a list in a task's output
    {{pipeline.inputs.key}}         a named pipeline input parameter (legacy)
    {{pipeline.goal}}               the pipeline goal string
    {{params.key}}                  a typed pipeline parameter (resolved before tasks run)
    {{session.key}}                 a value from the session blackboard
    {{item}}                        current element in a parallel_over loop

Special path segments
---------------------
    .first    — shorthand for [0] on any list; raises ResolutionError if empty
    .last     — shorthand for [-1] on any list; raises ResolutionError if empty
    Dict key lookup takes priority, so a dict with a key named "first" or "last"
    will be accessed by key, not treated as a list accessor.

Resolution rules
----------------
    Whole-value template:  "{{expr}}"          -> returns resolved value as-is
                                                  (preserves type: list, dict, etc.)
    Embedded template:     "prefix {{expr}} ..." -> string interpolation,
                                                    always returns str
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from trellis.exceptions import ResolutionError
from trellis.execution.blackboard import Blackboard, InMemoryBlackboard

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
    pipeline_params: dict[str, Any] = field(default_factory=dict)
    session:         dict[str, Any] = field(default_factory=dict)
    item:            Any            = None
    tenant_id:       str            = "default"
    blackboard:      Blackboard     = field(default_factory=InMemoryBlackboard)

    def set_task_output(self, task_id: str, output: Any) -> None:
        """Record a completed task's output in the context."""
        self.task_outputs[task_id] = output

    def with_params(self, params: dict[str, Any]) -> "ResolutionContext":
        """Return a shallow copy of this context with pipeline_params set."""
        import copy
        ctx = copy.copy(self)
        ctx.pipeline_params = params
        return ctx

    def with_item(self, item: Any) -> "ResolutionContext":
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

    Resolution order per segment:
      1. Dict-key lookup (preserves existing semantics for dicts with any key name).
      2. ``first`` / ``last`` on a list or tuple — shorthand for [0] / [-1].
      3. Attribute access.

    Raises ResolutionError if any segment cannot be resolved or if ``first`` /
    ``last`` is used on an empty sequence.
    """
    for segment in path:
        # Auto-parse JSON strings before attempting key/attribute lookup.
        # LLM tools (llm_job) return JSON as a plain string; accessing a
        # sub-key with e.g. {{task.output.segment_names}} should just work.
        # Handles fences + trailing prose by scanning for the first {/[ span.
        if isinstance(value, str) and segment not in ("first", "last"):
            _s = re.sub(r"^```[a-z]*\s*", "", value.strip(), flags=re.I)
            _s = re.sub(r"\s*```$", "", _s)
            _parsed = None
            try:
                _parsed = json.loads(_s)
            except Exception:
                _m = re.search(r"(\{.*\}|\[.*\])", value, flags=re.S)
                if _m:
                    try:
                        _parsed = json.loads(_m.group(0))
                    except Exception:
                        pass
            if isinstance(_parsed, (dict, list)):
                value = _parsed

        if isinstance(value, dict):
            if segment not in value:
                raise ResolutionError(
                    f"Cannot resolve {full_expr!r}: key {segment!r} not found "
                    f"in dict with keys {sorted(str(k) for k in value.keys())}."
                )
            value = value[segment]
        elif segment in ("first", "last") and isinstance(value, (list, tuple)):
            if not value:
                raise ResolutionError(
                    f"Cannot resolve {full_expr!r}: "
                    f"'.{segment}' accessed on an empty list."
                )
            value = value[0] if segment == "first" else value[-1]
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

    # {{params.key}} --------------------------------------------------------
    if parts[0] == "params":
        if len(parts) < 2:
            raise ResolutionError(
                f"Cannot resolve {{{{{expr}}}}}: 'params' requires a key name."
            )
        key = parts[1]
        if key not in ctx.pipeline_params:
            raise ResolutionError(
                f"Cannot resolve {{{{{expr}}}}}: param {key!r} not found. "
                f"Available params: {sorted(ctx.pipeline_params.keys()) or []}."
            )
        root = ctx.pipeline_params[key]
        return _walk_path(root, parts[2:], expr) if len(parts) > 2 else root

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
            expr = m.group(1).strip()
            resolved = _resolve_expr(expr, ctx)
            if not isinstance(resolved, (str, int, float, bool, type(None))):
                if type(resolved).__str__ is object.__str__:
                    raise ResolutionError(
                        f"Expression {{{{{expr}}}}} resolved to "
                        f"{type(resolved).__name__!r} in an embedded template. "
                        f"Objects without a string representation cannot be safely "
                        f"interpolated — access a specific text field instead "
                        f"(e.g. {{{{item.text}}}})."
                    )
            return str(resolved)

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