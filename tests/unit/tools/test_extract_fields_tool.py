"""Unit tests for the extract_fields tool."""

import pytest

from trellis.models.handles import FIELD_NOT_FOUND, FieldDefinition, SchemaHandle
from trellis.tools.impls.extract_fields import ExtractFieldsTool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_schema(*field_names: str, required: bool = True) -> SchemaHandle:
    return SchemaHandle(
        fields=[FieldDefinition(name=n, required=required) for n in field_names],
        source="test",
    )


def make_tool(llm_client=None) -> ExtractFieldsTool:
    return ExtractFieldsTool(llm_client=llm_client)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestExtractFieldsNoLLM:
    """Without an LLM client the tool stubs out all fields as not-found."""

    def test_all_fields_marked_not_found(self):
        tool = make_tool()
        schema = make_schema("revenue", "ebitda", "net_income")
        result = tool.execute(document="some text", schema=schema)
        assert result == {
            "revenue": FIELD_NOT_FOUND,
            "ebitda": FIELD_NOT_FOUND,
            "net_income": FIELD_NOT_FOUND,
        }

    def test_returns_only_declared_fields(self):
        tool = make_tool()
        schema = make_schema("ticker")
        result = tool.execute(document="text", schema=schema)
        assert set(result.keys()) == {"ticker"}

    def test_empty_schema_returns_empty_dict(self):
        tool = make_tool()
        schema = SchemaHandle(fields=[], source="empty")
        result = tool.execute(document="text", schema=schema)
        assert result == {}


class TestExtractFieldsInvalidSchema:
    def test_non_schema_raises_type_error(self):
        tool = make_tool()
        with pytest.raises(TypeError, match="SchemaHandle"):
            tool.execute(document="text", schema={"revenue": "number"})  # type: ignore[arg-type]


class TestExtractFieldsDocumentHandling:
    """The tool should accept varied document formats."""

    def test_string_document(self):
        tool = make_tool()
        schema = make_schema("field_a")
        result = tool.execute(document="plain text", schema=schema)
        assert "field_a" in result

    def test_document_with_pages_list_of_strings(self):
        class FakeDoc:
            pages = ["page 1 text", "page 2 text"]

        tool = make_tool()
        schema = make_schema("field_a")
        result = tool.execute(document=FakeDoc(), schema=schema)
        assert "field_a" in result

    def test_document_with_text_attribute(self):
        class FakeDoc:
            text = "document body"

        tool = make_tool()
        schema = make_schema("field_a")
        result = tool.execute(document=FakeDoc(), schema=schema)
        assert "field_a" in result

    def test_list_of_strings_document(self):
        tool = make_tool()
        schema = make_schema("x")
        result = tool.execute(document=["line 1", "line 2"], schema=schema)
        assert "x" in result


class TestExtractFieldsWithLLMClient:
    """Simulate a simple LLM client that returns canned values."""

    def test_llm_client_called_per_field(self):
        calls = []

        class StubLLM:
            def complete(self, prompt: str) -> str:
                calls.append(prompt)
                return "42"

        tool = ExtractFieldsTool(llm_client=StubLLM())
        schema = make_schema("revenue", "ebitda")
        result = tool.execute(document="net income 42", schema=schema)

        assert len(calls) == 2
        assert result["revenue"] == "42"
        assert result["ebitda"] == "42"

    def test_llm_exception_returns_not_found(self):
        class FailingLLM:
            def complete(self, prompt: str) -> str:
                raise RuntimeError("LLM unavailable")

        tool = ExtractFieldsTool(llm_client=FailingLLM())
        schema = make_schema("revenue")
        result = tool.execute(document="text", schema=schema)
        assert result["revenue"] == FIELD_NOT_FOUND


class TestExtractFieldsSectionFilter:
    """section_filter narrows extraction to fields belonging to that section."""

    def _schema_with_sections(self) -> SchemaHandle:
        return SchemaHandle(
            fields=[
                FieldDefinition(name="Total Revenues", section="face"),
                FieldDefinition(name="Operating Income", section="face"),
                FieldDefinition(name="Segment Revenue — [1]", section="segments"),
                FieldDefinition(name="Segment Revenue — [2]", section="segments"),
                FieldDefinition(name="Interest Income", section="other_income"),
                FieldDefinition(name="EPS — Basic ($)", section="per_share"),
            ],
            source="test",
        )

    def test_face_filter_returns_only_face_fields(self):
        tool = make_tool()
        schema = self._schema_with_sections()
        result = tool.execute(document="text", schema=schema, section_filter="face")
        assert set(result.keys()) == {"Total Revenues", "Operating Income"}

    def test_segments_filter_returns_only_segment_fields(self):
        tool = make_tool()
        schema = self._schema_with_sections()
        result = tool.execute(document="text", schema=schema, section_filter="segments")
        assert set(result.keys()) == {"Segment Revenue — [1]", "Segment Revenue — [2]"}

    def test_other_income_filter(self):
        tool = make_tool()
        schema = self._schema_with_sections()
        result = tool.execute(document="text", schema=schema, section_filter="other_income")
        assert set(result.keys()) == {"Interest Income"}

    def test_per_share_filter(self):
        tool = make_tool()
        schema = self._schema_with_sections()
        result = tool.execute(document="text", schema=schema, section_filter="per_share")
        assert set(result.keys()) == {"EPS — Basic ($)"}

    def test_unknown_section_returns_empty_dict(self):
        tool = make_tool()
        schema = self._schema_with_sections()
        result = tool.execute(document="text", schema=schema, section_filter="nonexistent")
        assert result == {}

    def test_no_filter_returns_all_fields(self):
        tool = make_tool()
        schema = self._schema_with_sections()
        result = tool.execute(document="text", schema=schema)
        assert len(result) == 6

    def test_section_filter_in_inputs_spec(self):
        tool = make_tool()
        assert "section_filter" in tool.get_inputs()
        assert tool.get_inputs()["section_filter"].required is False


class TestExtractFieldsInputSpec:
    def test_document_required(self):
        tool = make_tool()
        assert tool.get_inputs()["document"].required is True

    def test_schema_required(self):
        tool = make_tool()
        assert tool.get_inputs()["schema"].required is True

    def test_rules_optional(self):
        tool = make_tool()
        assert tool.get_inputs()["rules"].required is False

    def test_selector_optional(self):
        tool = make_tool()
        assert tool.get_inputs()["selector"].required is False
