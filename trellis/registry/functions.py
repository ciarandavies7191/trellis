"""FunctionRegistry — named deterministic function store for the compute tool.

Operators register Python callables at deploy time under a string name.
The DSL can then invoke them via `compute(function: "fiscal_period_logic", ...)`.

The trust boundary mirrors fetch_data's source registry: the model can only
reference registered function names; it cannot define or inject implementations.
"""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class RegisteredFunction:
    """
    A single entry in the FunctionRegistry.

    Attributes:
        name:           Registry name referenced in DSL ``compute`` tasks.
        fn:             The callable implementation. May be sync or async.
                        Signature: ``fn(**inputs) -> Any``
        input_schema:   Optional dict of {param_name: type_hint_string}.
                        Used for validation and model training context.
        output_schema:  Optional SchemaHandle or type_hint string describing
                        the return value shape. Surfaced in training examples
                        so the model can write correct downstream template refs.
        description:    Human-readable description of what the function does.
                        Included in system prompts during dataset generation.
    """

    name: str
    fn: Callable[..., Any]
    input_schema: dict[str, str] | None = None
    output_schema: Any = field(default=None)
    description: str | None = None


class FunctionRegistry:
    """
    Registry mapping names to deterministic Python callables.

    Usage::

        registry = FunctionRegistry()
        registry.register(RegisteredFunction(
            name="my_func",
            fn=lambda x: x * 2,
            description="Doubles a value",
        ))
        result = await registry.invoke("my_func", x=21)
        # → 42
    """

    def __init__(self) -> None:
        self._registry: dict[str, RegisteredFunction] = {}

    def register(self, entry: RegisteredFunction) -> None:
        """Register a function entry.

        Raises:
            ValueError: if *entry.name* is already registered.
        """
        if entry.name in self._registry:
            raise ValueError(f"Function {entry.name!r} is already registered.")
        self._registry[entry.name] = entry

    def get(self, name: str) -> RegisteredFunction:
        """Return the RegisteredFunction for *name*.

        Raises:
            ValueError: if *name* has not been registered.
        """
        if name not in self._registry:
            raise ValueError(
                f"Unknown compute function {name!r}. "
                f"Registered: {sorted(self._registry)}"
            )
        return self._registry[name]

    def names(self) -> list[str]:
        """Return a sorted list of all registered function names."""
        return sorted(self._registry)

    def __contains__(self, name: str) -> bool:
        return name in self._registry

    async def invoke(self, name: str, **inputs: Any) -> Any:
        """
        Invoke a registered function by name. Handles both sync and async
        implementations transparently.

        Raises:
            ValueError: if *name* is not registered.
        """
        entry = self.get(name)
        if inspect.iscoroutinefunction(entry.fn):
            return await entry.fn(**inputs)
        else:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, lambda: entry.fn(**inputs))
