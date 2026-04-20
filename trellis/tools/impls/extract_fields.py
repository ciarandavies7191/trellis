"""extract_fields tool — schema-bound field extraction from a document."""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict

try:
    import litellm  # type: ignore
except ImportError:  # pragma: no cover
    litellm = None  # type: ignore

from ..base import BaseTool, ToolInput, ToolOutput
from trellis.models.handles import FIELD_NOT_FOUND, FieldDefinition, SchemaHandle
from ..decorators import export_io

logger = logging.getLogger(__name__)

DEFAULT_EXTRACT_MODEL = os.getenv("EXTRACT_FIELDS_MODEL") or os.getenv(
    "EXTRACT_TEXT_MODEL", "openai/gpt-4o"
)

@export_io("debug/tools")
class ExtractFieldsTool(BaseTool):
    """
    Extract values from a document for every field declared in a SchemaHandle.

    Output is a dict ``{field_name: extracted_value | "__not_found__"}``.

    This tool uses a pluggable extraction backend. If an LLM client is provided
    at construction time it is used for extraction; otherwise the tool falls back
    to a stub implementation that returns ``"__not_found__"`` for all fields,
    suitable for unit testing without LLM credentials.
    """

    def __init__(self, name: str = "extract_fields", llm_client: Any = ...) -> None:
        super().__init__(name, "Schema-bound field extraction from a document")
        # ``...`` sentinel means "use litellm by default"; explicit None enables stub mode.
        self._stub_mode: bool = llm_client is None
        # A non-None, non-sentinel client is used via client.complete(prompt) per field.
        self._llm_client: Any = None if (llm_client is None or llm_client is ...) else llm_client

    def execute(
        self,
        document: Any,
        schema: SchemaHandle,
        rules: Any = None,
        selector: str | None = None,
        period_end: str | None = None,
        section_filter: str | None = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """
        Extract values for every field declared in *schema* from *document*.

        All fields are extracted in a single LLM call that returns a JSON object.
        Fields that cannot be located are set to FIELD_NOT_FOUND.

        Args:
            document:   A DocumentHandle, PageList, list of pages, or plain text string.
            schema:     A SchemaHandle declaring which fields to extract.
            rules:      Optional DocumentHandle containing extraction rules (e.g.
                        a spreading manual). Appended to the extraction prompt.
            selector:   Optional natural language hint to scope extraction to a
                        region of the document before field extraction.
            period_end:     Optional ISO date string (YYYY-MM-DD). When provided the
                            extraction prompt instructs the model to extract values for
                            this specific period only, ignoring other periods present
                            in the same document (e.g. prior-year comparatives in a
                            10-K or 10-Q).
            section_filter: Optional section name (e.g. "face", "segments",
                            "other_income", "per_share"). When provided, only fields
                            belonging to that section are extracted. Used when pages
                            have already been pre-selected per section, so that each
                            extraction call is tightly scoped.

        Returns:
            Dict mapping each field name in *schema* to its extracted value or
            ``FIELD_NOT_FOUND`` if the value could not be located.
        """
        if not isinstance(schema, SchemaHandle):
            raise TypeError(
                f"extract_fields: 'schema' must be a SchemaHandle, "
                f"got {type(schema).__name__!r}."
            )

        if not schema.fields:
            logger.warning("extract_fields: schema has no fields — returning empty dict")
            return {}

        # Narrow schema to requested section when section_filter is provided.
        # Creates a lightweight SchemaHandle view; does not mutate the original.
        if section_filter:
            section_fields = schema.fields_for_section(section_filter)
            if not section_fields:
                logger.warning(
                    "extract_fields: section_filter=%r matched no fields in schema — "
                    "returning empty dict",
                    section_filter,
                )
                return {}
            schema = SchemaHandle(
                fields=section_fields,
                source=schema.source,
                raw=schema.raw,
                task_id=schema.task_id,
            )

        if self._stub_mode:
            return {f.name: FIELD_NOT_FOUND for f in schema.fields}

        doc_text = self._to_text(document, selector=selector)
        rules_text = self._to_text(rules) if rules is not None else ""

        # Safety cap: ~400K chars ≈ 100K tokens, well inside a 128K context window
        # when combined with the field list, rules block, and system prompt.
        _MAX_DOC_CHARS = 400_000
        if len(doc_text) > _MAX_DOC_CHARS:
            logger.warning(
                "extract_fields: document text truncated from %d to %d chars "
                "to stay within context limits",
                len(doc_text), _MAX_DOC_CHARS,
            )
            doc_text = doc_text[:_MAX_DOC_CHARS]

        if self._llm_client is not None:
            return self._extract_with_client(
                doc_text=doc_text,
                fields=schema.fields,
                rules_text=rules_text,
            )

        model = kwargs.get("model", DEFAULT_EXTRACT_MODEL)
        result = self._extract_with_llm(
            doc_text=doc_text,
            fields=schema.fields,
            rules_text=rules_text,
            model=model,
            period_end=period_end,
        )

        # Ensure output only contains declared fields
        declared = set(schema.field_names())
        return {k: v for k, v in result.items() if k in declared}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _to_text(self, document: Any, selector: str | None = None) -> str:
        """Convert various document representations to a plain text string."""
        if isinstance(document, str):
            return document

        if hasattr(document, "pages") and isinstance(document.pages, list):
            parts: list[str] = []
            for page in document.pages:
                if isinstance(page, str):
                    parts.append(page)
                elif hasattr(page, "text"):
                    parts.append(page.text or "")
                elif isinstance(page, dict):
                    parts.append(page.get("text", ""))
            return "\n\n".join(p for p in parts if p)

        if hasattr(document, "text"):
            return document.text or ""

        if isinstance(document, list):
            parts = []
            for item in document:
                # Recurse so DocumentHandle (has .pages), PageList, strings, etc. all work.
                part = self._to_text(item)
                if part:
                    parts.append(part)
            return "\n\n".join(parts)

        return str(document)

    def _extract_with_client(
        self,
        doc_text: str,
        fields: list[FieldDefinition],
        rules_text: str,
    ) -> dict[str, Any]:
        """Extract each field individually using a custom llm_client.complete(prompt) interface."""
        result: dict[str, Any] = {}
        for f in fields:
            prompt = (
                f"Extract the value of '{f.name}' from the document. "
                f"Return only the value as a plain string.\n\n"
                f"Document:\n{doc_text}"
            )
            if rules_text.strip():
                prompt += f"\n\nRules:\n{rules_text}"
            try:
                value = self._llm_client.complete(prompt)
                result[f.name] = str(value) if value is not None else FIELD_NOT_FOUND
            except Exception:
                result[f.name] = FIELD_NOT_FOUND
        return result

    def _extract_with_llm(
        self,
        doc_text: str,
        fields: list[FieldDefinition],
        rules_text: str,
        model: str,
        period_end: str | None = None,
    ) -> dict[str, Any]:
        """
        Extract all field values in a single LLM call.

        Sends the full document text and the complete field list in one prompt,
        asking the model to return a JSON object. Avoids N separate LLM calls.
        Falls back to FIELD_NOT_FOUND for any field missing from the response.
        """
        if litellm is None:  # pragma: no cover
            raise RuntimeError("litellm is not installed. pip install litellm")

        field_list = "\n".join(
            f'  "{f.name}"'
            + (f'  // {f.type_hint}' if f.type_hint else "")
            for f in fields
        )

        rules_block = (
            f"\n\nExtraction rules / manual:\n{rules_text}\n"
            if rules_text.strip()
            else ""
        )

        period_block = (
            f"\n\nTarget period: Extract values for the reporting period ending {period_end}. "
            "SEC filings present multiple periods side by side (e.g. a 10-K shows 3 years; a "
            "10-Q shows current quarter and prior-year quarter). Extract ONLY the column for "
            f"the period ending {period_end}. Do not mix values from different periods. "
            "If this exact period is not present in the document, use the sentinel for all fields.\n"
            "\nCalculated rows — do NOT extract: gross_profit, gross_margin_pct, "
            "operating_margin_pct, net_margin_pct, effective_tax_rate, and any field "
            "whose name ends in '_pct', '_margin', '_growth', or '_rate'. "
            f"Set these fields to \"{FIELD_NOT_FOUND}\" — they will be computed deterministically.\n"
            if period_end
            else ""
        )

        system = (
            "You are a precise financial data extraction engine. "
            "Given document text and a list of field names, extract the value for every field. "
            "Return ONLY a valid JSON object where each key is a field name and each value is "
            "the extracted value as a string (numbers as strings, e.g. \"350018\"). "
            f'Use the sentinel "{FIELD_NOT_FOUND}" for any field you cannot locate. '
            "Do not include commentary, markdown fencing, or any text outside the JSON object.\n\n"
            "EXTRACTION RULES — follow exactly:\n"
            "RULE 1 — SEGMENT REVENUE vs SEGMENT OI: "
            "Fields labeled 'Segment Revenue' must be extracted from the Segment Information note "
            "(revenue column/table) or MD&A revenue disaggregation table. The Segment Information "
            "note contains BOTH a revenue table and an operating income table in separate columns "
            "or sections — always use the revenue column for Segment Revenue fields. Segment "
            "revenue values are typically 2–10x larger than segment operating income. Never "
            "substitute operating income values for revenue values.\n"
            "RULE 2 — SEGMENT OI: Fields labeled 'Segment OI' must be extracted ONLY from "
            "the Segment Information footnote, operating income by segment column/table. Do NOT "
            "use revenue figures here.\n"
            "RULE 3 — CALCULATED FIELDS: The following fields are computed programmatically "
            f"after extraction — ALWAYS return {FIELD_NOT_FOUND} for them, regardless of "
            "what appears in the document: any field whose label contains 'Gross Profit', "
            "'Gross Margin', 'Operating Margin', 'Net Margin', 'Effective Tax Rate', "
            "'YoY Growth', '(%)', or ends in a percentage unit. Do not read a calculated "
            "subtotal from the document and place it in these fields.\n"
            "RULE 4 — INVESTMENT GAINS ALIASES: The field 'Gains (Losses) on Investments, Net' "
            "may appear in the 'Other income (expense), net' footnote under alternative labels: "
            "'Net gain on equity securities', 'Gain (loss) on equity securities, net', "
            "'Net unrealized gain on investments', or 'Net gain (loss) on financial instruments'. "
            "Search the Other Income footnote for any of these labels and use the matching value.\n"
            "RULE 5 — INTEREST EXPENSE SIGN (manual v2 §5.2): The field 'Interest Expense' must "
            "be entered as a POSITIVE number (the absolute value / magnitude of the expense). The "
            "filing or OI&E note may present it as a negative figure — strip the sign and enter "
            "the absolute value. Do not enter a negative number for Interest Expense.\n"
            "RULE 6 — PERIOD ACCURACY: When a filing shows multiple periods side by side, "
            "extract values from the correct period column only. Cross-check your selection "
            "against the column header date before extracting.\n"
        )

        user = (
            f"Fields to extract:\n{{\n{field_list}\n}}"
            f"{period_block}"
            f"{rules_block}"
            f"\n\nDocument text:\n{doc_text}"
        )

        logger.debug(
            "extract_fields: calling %s for %d fields, doc_chars=%d",
            model, len(fields), len(doc_text),
        )

        try:
            resp = litellm.completion(
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                max_tokens=4096,
                response_format={"type": "json_object"},
                num_retries=6,
            )
            content = (resp.choices[0].message.content or "").strip()
        except Exception as exc:
            logger.warning("extract_fields: LLM call failed — %s: %s", type(exc).__name__, exc)
            return {f.name: FIELD_NOT_FOUND for f in fields}

        # Parse JSON response
        try:
            # Strip accidental code fences
            m = re.search(r"\{.*\}", content, flags=re.S)
            raw = json.loads(m.group(0) if m else content)
        except Exception:
            logger.warning("extract_fields: failed to parse LLM response as JSON — returning all not_found")
            return {f.name: FIELD_NOT_FOUND for f in fields}

        # Map back to field names, defaulting missing entries
        result: dict[str, Any] = {}
        for f in fields:
            result[f.name] = raw.get(f.name, FIELD_NOT_FOUND)
        return result

    def get_inputs(self) -> Dict[str, ToolInput]:
        return {
            "document": ToolInput(
                name="document",
                description="DocumentHandle, PageList, list of pages, or text string to extract from.",
                required=True,
            ),
            "schema": ToolInput(
                name="schema",
                description="A SchemaHandle declaring which fields to extract.",
                required=True,
            ),
            "rules": ToolInput(
                name="rules",
                description=(
                    "Optional DocumentHandle containing extraction rules / spreading manual. "
                    "Appended to the extraction prompt."
                ),
                required=False,
                default=None,
            ),
            "selector": ToolInput(
                name="selector",
                description="Optional natural language hint to scope extraction within the document.",
                required=False,
                default=None,
            ),
            "period_end": ToolInput(
                name="period_end",
                description=(
                    "Optional ISO date (YYYY-MM-DD). When set, extraction is scoped to the "
                    "column for this specific period; prior-year comparatives are ignored."
                ),
                required=False,
                default=None,
            ),
            "section_filter": ToolInput(
                name="section_filter",
                description=(
                    "Optional section name ('face', 'segments', 'other_income', 'per_share'). "
                    "When set, only fields with a matching section label are extracted. "
                    "Use when pages have been pre-selected per section to narrow scope."
                ),
                required=False,
                default=None,
            ),
            "model": ToolInput(
                name="model",
                description="litellm model override (default: EXTRACT_FIELDS_MODEL env var).",
                required=False,
                default=DEFAULT_EXTRACT_MODEL,
            ),
        }

    def get_output(self) -> ToolOutput:
        return ToolOutput(
            name="fields",
            description=(
                "Dict mapping field names to extracted values. "
                f"Fields not found are set to '{FIELD_NOT_FOUND}'."
            ),
            type_="object",
        )
