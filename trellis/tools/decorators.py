"""
Tool decorators for BaseTool subclasses.

Each decorator is applied at the class level and wraps BaseTool.execute() to
add cross-cutting behavior without modifying tool logic.

Decorators are composable — stack them in any order:

    @export_io(path="debug/tools")
    class SelectTool(BaseTool):
        ...

Adding future decorators follows the same pattern:

    @trace_timing()
    @export_io(path="debug/tools")
    class SelectTool(BaseTool):
        ...
"""

from __future__ import annotations

import dataclasses
import datetime
import json
import logging
import os
import pathlib
import threading
import uuid
from enum import Enum
from typing import Any, Callable, Type, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------

def _serialize(obj: Any, *, truncate_text: int = 2000, _depth: int = 0) -> Any:
    """
    Recursively convert an arbitrary tool value into a JSON-safe structure.

    Handles the domain types that flow between pipeline tools:
      - dataclasses  → dict (fields serialized recursively)
      - Enum         → primitive value
      - bytes        → {"$type": "bytes", "size": N}  (content skipped — can be MB)
      - pathlib.Path → str
      - datetime     → ISO-8601 string
      - list / tuple → list (elements serialized recursively)
      - dict         → dict (values serialized recursively)
      - str / int / float / bool / None → pass-through

    The ``truncate_text`` limit applies only to string values stored in a
    field literally named ``text`` on a dataclass (e.g. Page.text) to keep
    debug files readable.  All other strings are not truncated.

    A depth guard (_depth) prevents runaway recursion on deeply nested objects.
    """
    if _depth > 20:
        return f"<depth-limit: {type(obj).__name__}>"

    recurse = lambda v: _serialize(v, truncate_text=truncate_text, _depth=_depth + 1)  # noqa: E731

    if obj is None or isinstance(obj, (bool, int, float)):
        return obj

    if isinstance(obj, str):
        return obj

    if isinstance(obj, bytes):
        return {"$type": "bytes", "size": len(obj)}

    if isinstance(obj, Enum):
        return obj.value

    if isinstance(obj, (datetime.datetime, datetime.date)):
        return obj.isoformat()

    if isinstance(obj, pathlib.Path):
        return str(obj)

    if isinstance(obj, (list, tuple)):
        return [recurse(item) for item in obj]

    if isinstance(obj, dict):
        return {str(k): recurse(v) for k, v in obj.items()}

    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        result: dict[str, Any] = {"$type": type(obj).__name__}
        for f in dataclasses.fields(obj):
            val = getattr(obj, f.name)
            # Truncate text fields on Page-like objects to keep files readable
            if f.name == "text" and isinstance(val, str) and truncate_text > 0:
                serialized = val[:truncate_text] + ("…" if len(val) > truncate_text else "")
            else:
                serialized = recurse(val)
            result[f.name] = serialized
        return result

    # Fallback: repr so the file is still useful rather than crashing
    return f"<{type(obj).__name__}: {repr(obj)[:120]}>"


# ---------------------------------------------------------------------------
# export_io
# ---------------------------------------------------------------------------

def export_io(
    path: str | pathlib.Path = "debug",
    *,
    truncate_text: int = 2000,
) -> Callable[[Type[T]], Type[T]]:
    """
    Class decorator that writes each tool invocation's inputs and output to a
    JSON file for debugging.

    Args:
        path:          Directory to write files into. Created on first use.
        truncate_text: Max characters to include per ``Page.text`` value.
                       Set to 0 to disable truncation.

    Output file per invocation:
        {path}/{tool_name}_{counter:05d}_{uuid4[:8]}.json

    JSON structure::

        {
          "tool":       "select",
          "invocation": 3,
          "timestamp":  "2026-04-11T10:23:45.123456",
          "inputs":     { ... serialized kwargs ... },
          "output":     { ... serialized return value ... },
          "error":      null          # or error string on exception
        }

    Errors inside execute() are re-raised after the file is written.

    Example::

        @export_io(path="debug/tools")
        class SelectTool(BaseTool):
            ...
    """
    out_dir = pathlib.Path(path)
    _lock = threading.Lock()
    _counter: list[int] = [0]  # mutable container so the closure can mutate it

    def decorator(cls: Type[T]) -> Type[T]:
        original_execute = cls.execute  # type: ignore[attr-defined]

        def wrapped_execute(self: Any, *args: Any, **kwargs: Any) -> Any:
            with _lock:
                _counter[0] += 1
                invocation = _counter[0]

            tool_name = getattr(self, "name", cls.__name__)
            suffix = uuid.uuid4().hex[:8]
            filename = f"{tool_name}_{invocation:05d}_{suffix}.json"

            record: dict[str, Any] = {
                "tool": tool_name,
                "invocation": invocation,
                "timestamp": datetime.datetime.now().isoformat(),
                "inputs": _serialize(kwargs, truncate_text=truncate_text),
                "output": None,
                "error": None,
            }

            output = _UNSET = object()
            try:
                output = original_execute(self, *args, **kwargs)
                record["output"] = _serialize(output, truncate_text=truncate_text)
                return output
            except Exception as exc:
                record["error"] = f"{type(exc).__name__}: {exc}"
                raise
            finally:
                try:
                    out_dir.mkdir(parents=True, exist_ok=True)
                    dest = out_dir / filename
                    with open(dest, "w", encoding="utf-8") as fh:
                        json.dump(record, fh, indent=2, ensure_ascii=False)
                    logger.debug("export_io: wrote %s", dest)
                except Exception as write_exc:
                    logger.warning("export_io: failed to write %s — %s", filename, write_exc)

        cls.execute = wrapped_execute  # type: ignore[method-assign]
        return cls

    return decorator
