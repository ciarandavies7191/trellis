"""Unit tests for SchemaRegistry."""

import pytest

from trellis.models.handles import FieldDefinition, SchemaHandle
from trellis.registry.schema import SchemaRegistry


def make_schema(name: str = "test") -> SchemaHandle:
    return SchemaHandle(
        fields=[FieldDefinition(name="revenue"), FieldDefinition(name="ebitda")],
        source=name,
    )


class TestSchemaRegistry:
    def test_register_and_get(self):
        reg = SchemaRegistry()
        schema = make_schema("credit_memo_v2")
        reg.register("credit_memo_v2", schema)
        assert reg.get("credit_memo_v2") is schema

    def test_get_unknown_raises(self):
        reg = SchemaRegistry()
        with pytest.raises(KeyError, match="unknown_schema"):
            reg.get("unknown_schema")

    def test_duplicate_register_raises(self):
        reg = SchemaRegistry()
        reg.register("s1", make_schema())
        with pytest.raises(ValueError, match="already registered"):
            reg.register("s1", make_schema())

    def test_names_returns_sorted(self):
        reg = SchemaRegistry()
        reg.register("zzz", make_schema())
        reg.register("aaa", make_schema())
        reg.register("mmm", make_schema())
        assert reg.names() == ["aaa", "mmm", "zzz"]

    def test_contains(self):
        reg = SchemaRegistry()
        reg.register("my_schema", make_schema())
        assert "my_schema" in reg
        assert "other" not in reg

    def test_empty_registry(self):
        reg = SchemaRegistry()
        assert reg.names() == []
