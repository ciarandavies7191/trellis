"""Unit tests for FunctionRegistry, including sync/async dispatch."""

import asyncio

import pytest

from trellis.registry.functions import FunctionRegistry, RegisteredFunction


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def sync_double(x: int) -> int:
    return x * 2


async def async_triple(x: int) -> int:
    await asyncio.sleep(0)  # yield to event loop
    return x * 3


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestFunctionRegistry:
    def test_register_and_get(self):
        reg = FunctionRegistry()
        reg.register(RegisteredFunction(name="double", fn=sync_double))
        entry = reg.get("double")
        assert entry.name == "double"
        assert entry.fn is sync_double

    def test_get_unknown_raises(self):
        reg = FunctionRegistry()
        with pytest.raises(ValueError, match="Unknown compute function"):
            reg.get("nonexistent")

    def test_duplicate_register_raises(self):
        reg = FunctionRegistry()
        reg.register(RegisteredFunction(name="f", fn=sync_double))
        with pytest.raises(ValueError, match="already registered"):
            reg.register(RegisteredFunction(name="f", fn=sync_double))

    def test_names_sorted(self):
        reg = FunctionRegistry()
        reg.register(RegisteredFunction(name="zzz", fn=sync_double))
        reg.register(RegisteredFunction(name="aaa", fn=sync_double))
        assert reg.names() == ["aaa", "zzz"]

    def test_contains(self):
        reg = FunctionRegistry()
        reg.register(RegisteredFunction(name="double", fn=sync_double))
        assert "double" in reg
        assert "triple" not in reg

    @pytest.mark.asyncio
    async def test_invoke_sync_function(self):
        reg = FunctionRegistry()
        reg.register(RegisteredFunction(name="double", fn=sync_double))
        result = await reg.invoke("double", x=7)
        assert result == 14

    @pytest.mark.asyncio
    async def test_invoke_async_function(self):
        reg = FunctionRegistry()
        reg.register(RegisteredFunction(name="triple", fn=async_triple))
        result = await reg.invoke("triple", x=5)
        assert result == 15

    @pytest.mark.asyncio
    async def test_invoke_unknown_raises(self):
        reg = FunctionRegistry()
        with pytest.raises(ValueError, match="Unknown compute function"):
            await reg.invoke("missing", x=1)

    def test_registered_function_metadata(self):
        entry = RegisteredFunction(
            name="my_fn",
            fn=sync_double,
            input_schema={"x": "int"},
            output_schema="int",
            description="Doubles a value",
        )
        assert entry.input_schema == {"x": "int"}
        assert entry.output_schema == "int"
        assert entry.description == "Doubles a value"
