"""SchemaRegistry — named schema store for the load_schema tool.

Operators register SchemaHandle instances at deploy time under a string name.
The DSL can then reference them by name via `load_schema(source: "credit_memo_v2")`.
"""

from __future__ import annotations

from trellis.models.handles import SchemaHandle


class SchemaRegistry:
    """
    A simple name → SchemaHandle registry.

    Usage::

        registry = SchemaRegistry()
        registry.register("credit_memo_v2", my_schema_handle)
        schema = registry.get("credit_memo_v2")
    """

    def __init__(self) -> None:
        self._registry: dict[str, SchemaHandle] = {}

    def register(self, name: str, schema: SchemaHandle) -> None:
        """Register a SchemaHandle under *name*.

        Raises:
            ValueError: if *name* is already registered.
        """
        if name in self._registry:
            raise ValueError(f"Schema {name!r} is already registered.")
        self._registry[name] = schema

    def get(self, name: str) -> SchemaHandle:
        """Return the SchemaHandle for *name*.

        Raises:
            KeyError: if *name* has not been registered.
        """
        if name not in self._registry:
            raise KeyError(
                f"Unknown schema {name!r}. "
                f"Registered: {sorted(self._registry)}"
            )
        return self._registry[name]

    def names(self) -> list[str]:
        """Return a sorted list of all registered schema names."""
        return sorted(self._registry)

    def __contains__(self, name: str) -> bool:
        return name in self._registry
