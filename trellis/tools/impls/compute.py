"""compute tool — invoke a named deterministic function from the FunctionRegistry."""

from __future__ import annotations

import asyncio
from typing import Any, Dict

from ..base import BaseTool, ToolInput, ToolOutput
from ..decorators import export_io


@export_io(path="debug/tools")
class ComputeTool(BaseTool):
    """
    Invoke a named deterministic function from a FunctionRegistry.

    This tool is the single DSL surface for all deterministic, codeable
    computations (date arithmetic, ticker resolution, currency normalization,
    etc.). It does not accept code strings — the function definition lives
    in the operator's registry; the DSL only names it.

    Usage in DSL::

        - id: resolve_periods
          tool: compute
          inputs:
            function: fiscal_period_logic
            as_of_date: "{{pipeline.inputs.as_of_period}}"
            company: "{{pipeline.inputs.company}}"
    """

    def __init__(
        self,
        name: str = "compute",
        function_registry: Any = None,
    ) -> None:
        super().__init__(name, "Invoke a named deterministic function from the FunctionRegistry")
        self._function_registry = function_registry

    async def execute_async(self, function: str, **kwargs: Any) -> Any:
        """
        Async entry point — invokes the registered function and returns its result.

        Args:
            function: Registered function name.
            **kwargs: Additional key-value inputs forwarded to the function.

        Returns:
            Whatever the registered function returns.

        Raises:
            ValueError: if *function* is not registered or no registry is set.
        """
        if self._function_registry is None:
            raise ValueError(
                "ComputeTool has no FunctionRegistry. "
                "Provide one via function_registry= at construction time."
            )
        return await self._function_registry.invoke(function, **kwargs)

    def execute(self, function: str, **kwargs: Any) -> Any:
        """
        Sync wrapper — runs execute_async in a new event loop if needed.

        The async path is preferred; this is a convenience shim so the tool
        can be registered with AsyncToolRegistry which calls execute() synchronously
        and wraps it in asyncio.to_thread.
        """
        if self._function_registry is None:
            raise ValueError(
                "ComputeTool has no FunctionRegistry. "
                "Provide one via function_registry= at construction time."
            )

        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        if loop.is_running():
            # We are inside an existing event loop; use run_in_executor to
            # avoid nesting event loops.
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(
                    lambda: asyncio.run(
                        self._function_registry.invoke(function, **kwargs)
                    )
                )
                return future.result()
        else:
            return loop.run_until_complete(
                self._function_registry.invoke(function, **kwargs)
            )

    def get_inputs(self) -> Dict[str, ToolInput]:
        return {
            "function": ToolInput(
                name="function",
                description="Registered function name from the FunctionRegistry.",
                required=True,
            ),
        }

    def get_output(self) -> ToolOutput:
        return ToolOutput(
            name="result",
            description=(
                "The return value of the registered function. "
                "Output shape depends on the function's declared output_schema."
            ),
            type_="any",
        )
