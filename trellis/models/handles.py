"""
First-class handle types for structured pipeline data.

Defines:
  - FieldDefinition  — a single typed field in a schema
  - SchemaHandle     — a schema object that flows through the pipeline graph
  - PeriodDescriptor — a resolved filing period produced by fiscal_period_logic
  - FIELD_NOT_FOUND  — sentinel emitted by extract_fields when a field cannot be located
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Sentinel
# ---------------------------------------------------------------------------

#: Emitted by extract_fields when a field value cannot be located in the source
#: document. Downstream tasks should check for this value rather than treating
#: it as a valid extracted result.
FIELD_NOT_FOUND: str = "__not_found__"


# ---------------------------------------------------------------------------
# Schema primitives
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FieldDefinition:
    """
    A single field in a schema.

    Attributes:
        name:             Field name. Must be unique within the schema.
        type_hint:        Optional string hint for the expected value type
                          (e.g. "number", "string", "date"). Used for validation
                          and extraction guidance. Not enforced as a hard type
                          constraint.
        required:         Whether the field must be present in conformant output.
                          Default True.
        description:      Optional human-readable description of the field's
                          semantics, passed to extract_fields as context.
        computed:         True if this field is derived by a compute step (e.g.
                          Gross Margin, Net Margin). Computed fields must NEVER
                          be extraction targets — they are owned by compute_derived.
        formula:          Optional expression describing the computation
                          (e.g. "gross_profit / total_revenue * 100"). Informational.
        sign_convention:  Optional sign rule, e.g. "negative" for Interest Expense.
                          Injected into extraction prompts as a constraint.
        section:          Logical section of the income statement this field belongs
                          to: "face", "segments", "other_income", or "per_share".
                          Used by filter_schema_by_section().
        cross_check:      Optional arithmetic constraint string. Used by
                          validate_cross_checks() to verify extracted values.
        fallback_rule:    Optional rule applied when the company's presentation
                          differs from the expected label (e.g. single SG&A line).
        manual_ref:       Reference to the spreading manual section, e.g. "§3.1".
    """

    name: str
    type_hint: str | None = None
    required: bool = True
    description: str | None = None
    computed: bool = False
    formula: str | None = None
    sign_convention: str | None = None
    section: str | None = None
    cross_check: str | None = None
    fallback_rule: str | None = None
    manual_ref: str | None = None


@dataclass
class SchemaHandle:
    """
    A first-class schema object that flows through the pipeline as task output.

    Produced by:
      - load_schema tool (from file, URL, registered name, or DocumentHandle)
      - llm_job tool (when a task is designed to infer/emit a schema)

    Consumed by:
      - extract_fields tool (bounds extraction to declared fields)
      - export tool (validates output conformance before writing)

    Attributes:
        fields:      Ordered list of FieldDefinitions.
        source:      Human-readable provenance string (filename, registry name, etc.)
        raw:         Original source representation (dict, file bytes, etc.)
                     Retained for export tools that need to render into the
                     original template file (e.g. populating an Excel template).
        task_id:     ID of the pipeline task that produced this handle.
    """

    fields: list[FieldDefinition]
    source: str
    raw: Any = field(default=None, repr=False)
    task_id: str | None = None

    def field_names(self) -> list[str]:
        """Return a list of all field names in declaration order."""
        return [f.name for f in self.fields]

    def required_field_names(self) -> list[str]:
        """Return a list of names for fields marked required=True."""
        return [f.name for f in self.fields if f.required]

    def computed_field_names(self) -> list[str]:
        """Return names of fields that are computed, not extracted."""
        return [f.name for f in self.fields if f.computed]

    def extractable_fields(self) -> list[FieldDefinition]:
        """Return fields that should be extracted (computed=False)."""
        return [f for f in self.fields if not f.computed]

    def fields_for_section(self, section: str) -> list[FieldDefinition]:
        """Return fields belonging to a named section (face/segments/other_income/per_share)."""
        return [f for f in self.fields if f.section == section]

    def to_extraction_context(self) -> str:
        """
        Render extractable (non-computed) fields as a compact string suitable
        for injection into an llm_job or extract_fields prompt.
        Format: one field per line — name | type_hint | description | sign | fallback.
        Computed fields are excluded; they must never be extraction targets.
        """
        lines: list[str] = []
        for f in self.fields:
            if f.computed:
                continue
            parts: list[str] = [f.name]
            if f.type_hint:
                parts.append(f.type_hint)
            if f.description:
                parts.append(f.description)
            if f.sign_convention:
                parts.append(f"sign: {f.sign_convention}")
            if f.fallback_rule:
                parts.append(f"fallback: {f.fallback_rule}")
            lines.append(" | ".join(parts))
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Period descriptor (produced by fiscal_period_logic compute function)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PeriodDescriptor:
    """
    A resolved filing period produced by fiscal_period_logic.

    Attributes:
        label:        Human-readable label, e.g. "Q1 2025", "FY 2024".
        period_end:   Period end date as ISO string, e.g. "2025-03-31".
        period_type:  One of "annual", "ytd_current", "ytd_prior".
        is_annual:    True if this is a full-year filing.
    """

    label: str
    period_end: str
    period_type: str
    is_annual: bool