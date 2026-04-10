"""Unit tests for the load_schema tool."""

import pytest

from trellis.models.handles import FieldDefinition, SchemaHandle
from trellis.registry.schema import SchemaRegistry
from trellis.tools.impls.load_schema import LoadSchemaTool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_tool(registry: SchemaRegistry | None = None) -> LoadSchemaTool:
    return LoadSchemaTool(schema_registry=registry)


def make_registry(*names: str) -> SchemaRegistry:
    reg = SchemaRegistry()
    for name in names:
        reg.register(
            name,
            SchemaHandle(
                fields=[FieldDefinition(name="field_a"), FieldDefinition(name="field_b")],
                source=name,
            ),
        )
    return reg


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestLoadSchemaFromRegistry:
    def test_returns_registered_schema(self):
        reg = make_registry("credit_memo_v2")
        tool = make_tool(reg)
        result = tool.execute(source="credit_memo_v2")
        assert isinstance(result, SchemaHandle)
        assert result.source == "credit_memo_v2"

    def test_field_names_preserved(self):
        reg = make_registry("my_schema")
        tool = make_tool(reg)
        result = tool.execute(source="my_schema")
        assert result.field_names() == ["field_a", "field_b"]

    def test_unknown_name_without_registry_returns_empty_schema(self):
        tool = make_tool()
        result = tool.execute(source="unknown_name")
        assert isinstance(result, SchemaHandle)
        assert result.fields == []


class TestLoadSchemaFromDict:
    def test_field_name_type_dict(self):
        tool = make_tool()
        result = tool.execute(source={"revenue": "number", "ebitda": "number"})
        assert isinstance(result, SchemaHandle)
        names = result.field_names()
        assert "revenue" in names
        assert "ebitda" in names

    def test_fields_key_dict(self):
        tool = make_tool()
        result = tool.execute(source={
            "fields": [
                {"name": "ticker", "type_hint": "string", "required": True},
                {"name": "price", "type_hint": "number", "required": False},
            ]
        })
        assert result.field_names() == ["ticker", "price"]
        assert result.required_field_names() == ["ticker"]

    def test_raw_preserved(self):
        data = {"net_income": "number"}
        tool = make_tool()
        result = tool.execute(source=data)
        assert result.raw == data


class TestLoadSchemaFromList:
    def test_list_of_strings(self):
        tool = make_tool()
        result = tool.execute(source=["revenue", "ebitda", "net_income"])
        assert result.field_names() == ["revenue", "ebitda", "net_income"]

    def test_list_of_dicts(self):
        tool = make_tool()
        result = tool.execute(source=[
            {"name": "total_assets", "type_hint": "number"},
            {"name": "total_liabilities", "type_hint": "number"},
        ])
        assert "total_assets" in result.field_names()
        assert "total_liabilities" in result.field_names()

    def test_list_of_field_definitions(self):
        tool = make_tool()
        fields = [FieldDefinition(name="x"), FieldDefinition(name="y")]
        result = tool.execute(source=fields)
        assert result.field_names() == ["x", "y"]


class TestLoadSchemaPassthrough:
    def test_existing_schema_handle_returned_as_is(self):
        tool = make_tool()
        existing = SchemaHandle(
            fields=[FieldDefinition(name="foo")],
            source="original",
        )
        result = tool.execute(source=existing)
        assert result is existing


class TestLoadSchemaFromDocument:
    def test_document_with_columns_metadata(self):
        class FakeDoc:
            filename = "template.xlsx"
            metadata = {"columns": ["date", "revenue", "ebitda"]}

        tool = make_tool()
        result = tool.execute(source=FakeDoc())
        assert result.field_names() == ["date", "revenue", "ebitda"]

    def test_document_with_columns_attribute(self):
        class FakeDoc:
            filename = "template.csv"
            metadata = {}
            columns = ["ticker", "price", "volume"]

        tool = make_tool()
        result = tool.execute(source=FakeDoc())
        assert result.field_names() == ["ticker", "price", "volume"]

    def test_document_without_columns_returns_empty_fields(self):
        class FakeDoc:
            filename = "doc.pdf"
            text = "Some text without structure"

        tool = make_tool()
        result = tool.execute(source=FakeDoc())
        assert isinstance(result, SchemaHandle)
        assert result.fields == []


class TestLoadSchemaTaskId:
    def test_task_id_stamped(self):
        tool = make_tool()
        result = tool.execute(source=["a", "b"], task_id="load_schema_1")
        assert result.task_id == "load_schema_1"


class TestLoadSchemaInputSpec:
    def test_source_required(self):
        tool = make_tool()
        inputs = tool.get_inputs()
        assert inputs["source"].required is True

    def test_hint_optional(self):
        tool = make_tool()
        inputs = tool.get_inputs()
        assert inputs["hint"].required is False
