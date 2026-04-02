"""Async-capable tool registry and discovery.

Provides:
- AsyncToolRegistry: single registry for async/sync callables and BaseTool adapters
- discover_impls(): auto-register tools from trellis.tools.impls

Backwards compatibility: retains ToolRegistryManager for metadata queries,
but prefer AsyncToolRegistry for execution.
"""

from __future__ import annotations

import asyncio
import inspect
import importlib
import pkgutil
from typing import Any, Callable, Dict, List, Optional, Type

from .base import BaseTool

# ---------------------------------------------------------------------------
# Async-capable tool registry (explicit names only)
# ---------------------------------------------------------------------------


class AsyncToolRegistry:
    """
    Registry that can invoke both async and sync tool implementations.

    Explicit tool names only — no aliasing. DSL must reference implementation names
    (e.g., "llm", "fetch", "document").

    - register_callable(name, fn): register any callable (sync or async)
    - register_tool(tool): register a BaseTool; wraps sync execute in a thread
    - invoke(name, inputs): awaitable invocation
    - registered_tools(): sorted list of names
    """

    def __init__(self) -> None:
        self._callables: dict[str, Callable[..., Any]] = {}
        self._tools_by_name: dict[str, BaseTool] = {}

    # ---------------------------- Registration ----------------------------

    def register_callable(self, name: str, fn: Callable[..., Any]) -> None:
        self._callables[name] = fn

    def register_tool(self, tool: BaseTool) -> None:
        # Store original tool instance
        self._tools_by_name[tool.name] = tool

        # Register callable adapter for execute()
        def _sync_adapter(**kwargs: Any) -> Any:
            return tool.execute(**kwargs)

        self._callables[tool.name] = _sync_adapter

    # ------------------------------ Lookup -------------------------------

    def registered_tools(self) -> List[str]:
        return sorted(self._callables.keys())

    # ------------------------------ Invoke -------------------------------

    async def invoke(self, name: str, inputs: Dict[str, Any]) -> Any:
        if name not in self._callables:
            raise KeyError(
                f"Tool {name!r} is not registered. "
                f"Registered tools: {sorted(self._callables.keys())}"
            )
        fn = self._callables[name]
        if inspect.iscoroutinefunction(fn):
            return await fn(**inputs)
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: fn(**inputs))

    # ----------------------------- Discovery -----------------------------

    def discover_impls(self) -> None:
        """
        Import all modules under trellis.tools.impls and auto-register BaseTool subclasses.
        """
        pkg_name = "trellis.tools.impls"
        try:
            pkg = importlib.import_module(pkg_name)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"Failed to import {pkg_name}: {exc}") from exc

        for mod_info in pkgutil.iter_modules(getattr(pkg, "__path__", []), pkg_name + "."):
            module = importlib.import_module(mod_info.name)
            for _, obj in inspect.getmembers(module, inspect.isclass):
                if not issubclass(obj, BaseTool) or obj is BaseTool:
                    continue
                # Try default constructor; if it fails, skip auto-register
                try:
                    tool: BaseTool = obj()  # type: ignore[call-arg]
                except Exception:
                    continue
                self.register_tool(tool)


# ---------------------------------------------------------------------------
# Back-compat metadata manager (non-executing)
# ---------------------------------------------------------------------------


class ToolRegistryManager:
    """Retained for metadata queries; prefer AsyncToolRegistry for execution."""

    def __init__(self):
        self._tools: Dict[str, BaseTool] = {}
        self._tool_classes: Dict[str, Type[BaseTool]] = {}

    def register_tool(self, tool: BaseTool) -> None:
        self._tools[tool.name] = tool

    def register_tool_class(self, name: str, tool_class: Type[BaseTool]) -> None:
        self._tool_classes[name] = tool_class

    def get_tool(self, name: str) -> Optional[BaseTool]:
        if name in self._tools:
            return self._tools[name]
        if name in self._tool_classes:
            tool = self._tool_classes[name]()
            self._tools[name] = tool
            return tool
        return None

    def list_available_tools(self) -> List[str]:
        return list(set(list(self._tools.keys()) + list(self._tool_classes.keys())))

    def get_tool_metadata(self, name: str) -> Optional[Dict]:
        tool = self.get_tool(name)
        if tool:
            return {
                "name": tool.name,
                "description": tool.description,
                "inputs": tool.get_inputs(),
                "output": tool.get_output(),
            }
        return None


def build_default_registry() -> AsyncToolRegistry:
    """Create an AsyncToolRegistry with discovered implementations (no aliases)."""
    reg = AsyncToolRegistry()
    reg.discover_impls()
    return reg
