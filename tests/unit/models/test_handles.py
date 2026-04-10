"""Unit tests for SchemaHandle, FieldDefinition, and PeriodDescriptor."""

import pytest

from trellis.models.handles import (
    FIELD_NOT_FOUND,
    FieldDefinition,
    PeriodDescriptor,
    SchemaHandle,
)


class TestFieldDefinition:
    def test_defaults(self):
        fd = FieldDefinition(name="revenue")
        assert fd.name == "revenue"
        assert fd.type_hint is None
        assert fd.required is True
        assert fd.description is None

    def test_full_construction(self):
        fd = FieldDefinition(
            name="total_assets",
            type_hint="number",
            required=False,
            description="Total assets in USD millions",
        )
        assert fd.type_hint == "number"
        assert fd.required is False

    def test_frozen(self):
        fd = FieldDefinition(name="ebitda")
        with pytest.raises((AttributeError, TypeError)):
            fd.name = "new_name"  # type: ignore[misc]


class TestSchemaHandle:
    def _make_schema(self):
        return SchemaHandle(
            fields=[
                FieldDefinition(name="revenue", type_hint="number"),
                FieldDefinition(name="ebitda", type_hint="number"),
                FieldDefinition(name="notes", type_hint="string", required=False),
            ],
            source="test_schema",
        )

    def test_field_names(self):
        s = self._make_schema()
        assert s.field_names() == ["revenue", "ebitda", "notes"]

    def test_required_field_names(self):
        s = self._make_schema()
        assert s.required_field_names() == ["revenue", "ebitda"]

    def test_to_extraction_context_includes_all_fields(self):
        s = self._make_schema()
        ctx = s.to_extraction_context()
        assert "revenue" in ctx
        assert "ebitda" in ctx
        assert "notes" in ctx

    def test_to_extraction_context_format(self):
        s = SchemaHandle(
            fields=[
                FieldDefinition(
                    name="net_income",
                    type_hint="number",
                    description="Net income after tax",
                )
            ],
            source="test",
        )
        ctx = s.to_extraction_context()
        assert ctx == "net_income | number | Net income after tax"

    def test_to_extraction_context_name_only(self):
        s = SchemaHandle(
            fields=[FieldDefinition(name="ticker")],
            source="test",
        )
        assert s.to_extraction_context() == "ticker"

    def test_task_id_optional(self):
        s = SchemaHandle(fields=[], source="x")
        assert s.task_id is None
        s.task_id = "load_schema_1"
        assert s.task_id == "load_schema_1"

    def test_raw_not_in_repr(self):
        s = SchemaHandle(fields=[], source="x", raw=b"\x00" * 1000)
        r = repr(s)
        assert "raw" not in r


class TestPeriodDescriptor:
    def test_construction(self):
        pd = PeriodDescriptor(
            label="Q1 2025",
            period_end="2025-03-31",
            period_type="ytd_current",
            is_annual=False,
        )
        assert pd.label == "Q1 2025"
        assert pd.is_annual is False

    def test_frozen(self):
        pd = PeriodDescriptor(
            label="FY 2024",
            period_end="2024-12-31",
            period_type="annual",
            is_annual=True,
        )
        with pytest.raises((AttributeError, TypeError)):
            pd.label = "FY 2025"  # type: ignore[misc]


class TestFieldNotFoundSentinel:
    def test_value(self):
        assert FIELD_NOT_FOUND == "__not_found__"

    def test_is_string(self):
        assert isinstance(FIELD_NOT_FOUND, str)
