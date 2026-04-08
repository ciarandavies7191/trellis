"""Tests for AsyncToolRegistry discovery and explicit naming (no aliases)."""

import pytest

from trellis.tools.registry import AsyncToolRegistry


@pytest.mark.asyncio
async def test_discovery_explicit_names():
    reg = AsyncToolRegistry()
    reg.discover_impls()

    # Implementations should be registered by their implementation names only
    expected = {"llm_job", "fetch_data", "ingest_document", "mock"}
    registered = set(reg.registered_tools())
    assert expected.issubset(registered)

    # Do not invoke llm_job here to avoid network/API dependencies


@pytest.mark.asyncio
async def test_register_callable_and_sync_adapter():
    reg = AsyncToolRegistry()

    # Direct callable (async)
    async def echo(**kwargs):
        return kwargs

    reg.register_callable("echo", echo)
    out = await reg.invoke("echo", {"x": 1})
    assert out == {"x": 1}

    # Discovery should register built-in tools without errors
    reg.discover_impls()
    assert "llm_job" in reg.registered_tools()
