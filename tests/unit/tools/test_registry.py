"""Tests for AsyncToolRegistry discovery and explicit naming (no aliases)."""

import pytest

from trellis.tools.registry import AsyncToolRegistry


@pytest.mark.asyncio
async def test_discovery_explicit_names():
    reg = AsyncToolRegistry()
    reg.discover_impls()

    # Implementations should be registered by their implementation names only
    for impl in ["llm_job", "fetch", "document", "mock"]:
        assert impl in reg.registered_tools()

    # Invoking an implementation name should work
    out = await reg.invoke("llm_job", {"prompt": "hi"})
    assert isinstance(out, str) and "LLM response" in out


@pytest.mark.asyncio
async def test_register_callable_and_sync_adapter():
    reg = AsyncToolRegistry()

    # Direct callable (async)
    async def echo(**kwargs):
        return kwargs

    reg.register_callable("echo", echo)
    out = await reg.invoke("echo", {"x": 1})
    assert out == {"x": 1}

    # Register BaseTool (sync execute) via discovery alternative
    reg.discover_impls()
    out2 = await reg.invoke("llm_job", {"prompt": "test"})
    assert isinstance(out2, str)
