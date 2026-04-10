"""Integration tests for the compute tool."""

import asyncio

import pytest

from trellis.registry.finance_functions import build_finance_registry
from trellis.registry.functions import FunctionRegistry, RegisteredFunction
from trellis.tools.impls.compute import ComputeTool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_tool(registry: FunctionRegistry | None = None) -> ComputeTool:
    return ComputeTool(function_registry=registry or build_finance_registry())


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestComputeToolInit:
    def test_name(self):
        tool = ComputeTool()
        assert tool.name == "compute"

    def test_no_registry_raises_on_execute(self):
        tool = ComputeTool(function_registry=None)
        with pytest.raises(ValueError, match="no FunctionRegistry"):
            tool.execute(function="fiscal_period_logic", as_of_date="2024-12-31", company="amazon")


class TestComputeToolExecuteSync:
    def test_ticker_lookup(self):
        tool = make_tool()
        result = tool.execute(function="ticker_lookup", company="apple")
        assert result == "AAPL"

    def test_fiscal_year_end(self):
        tool = make_tool()
        result = tool.execute(function="fiscal_year_end", company="microsoft")
        assert result == "06-30"

    def test_period_label(self):
        tool = make_tool()
        result = tool.execute(function="period_label", date="2024-03-31", period_type="quarterly")
        assert result == "Q1 2024"

    def test_financial_scale_normalize(self):
        tool = make_tool()
        result = tool.execute(
            function="financial_scale_normalize",
            value=2_000_000,
            source_currency="USD",
            target_currency="USD",
            target_scale="millions",
        )
        assert result == pytest.approx(2.0)

    def test_fiscal_period_logic_annual(self):
        tool = make_tool()
        result = tool.execute(
            function="fiscal_period_logic",
            as_of_date="2024-12-31",
            company="amazon",
        )
        assert len(result) == 1
        assert result[0].is_annual is True

    def test_unknown_function_raises(self):
        tool = make_tool()
        with pytest.raises(ValueError, match="Unknown compute function"):
            tool.execute(function="nonexistent_fn")


class TestComputeToolAsync:
    @pytest.mark.asyncio
    async def test_async_function_dispatch(self):
        reg = FunctionRegistry()

        async def async_add(a: int, b: int) -> int:
            await asyncio.sleep(0)
            return a + b

        reg.register(RegisteredFunction(name="async_add", fn=async_add))
        result = await reg.invoke("async_add", a=3, b=4)
        assert result == 7


class TestComputeToolGetInputs:
    def test_function_input_declared(self):
        tool = ComputeTool()
        inputs = tool.get_inputs()
        assert "function" in inputs
        assert inputs["function"].required is True


class TestComputeToolGetOutput:
    def test_output_defined(self):
        tool = ComputeTool()
        out = tool.get_output()
        assert out.name == "result"
