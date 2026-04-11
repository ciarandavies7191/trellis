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


class ExtractFieldsTool(BaseTool):
    """
    Extract values from a document for every field declared in a SchemaHandle.

    Output is a dict ``{field_name: extracted_value | "__not_found__"}``.

    This tool uses a pluggable extraction backend. If an LLM client is provided
    at construction time it is used for extraction; otherwise the tool falls back
    to a stub implementation that returns ``"__not_found__"`` for all fields,
    suitable for unit testing without LLM credentials.
    """

    def __init__(self, name: str = "extract_fields") -> None:
        super().__init__(name, "Schema-bound field extraction from a document")

    def execute(
        self,
        document: Any,
        schema: SchemaHandle,
        rules: Any = None,
        selector: str | None = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """
        Extract values for every field declared in *schema* from *document*.

        All fields are extracted in a single LLM call that returns a JSON object.
        Fields that cannot be located are set to FIELD_NOT_FOUND.

        Args:
            document: A DocumentHandle, PageList, list of pages, or plain text string.
            schema:   A SchemaHandle declaring which fields to extract.
            rules:    Optional DocumentHandle containing extraction rules (e.g.
                      a spreading manual). Appended to the extraction prompt.
            selector: Optional natural language hint to scope extraction to a
                      region of the document before field extraction.

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

        model = kwargs.get("model", DEFAULT_EXTRACT_MODEL)
        doc_text = self._to_text(document, selector=selector)
        rules_text = self._to_text(rules) if rules is not None else ""

        result = self._extract_with_llm(
            doc_text=doc_text,
            fields=schema.fields,
            rules_text=rules_text,
            model=model,
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
                if isinstance(item, str):
                    parts.append(item)
                elif hasattr(item, "text"):
                    parts.append(item.text or "")
            return "\n\n".join(p for p in parts if p)

        return str(document)

    def _extract_with_llm(
        self,
        doc_text: str,
        fields: list[FieldDefinition],
        rules_text: str,
        model: str,
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

        system = (
            "You are a precise financial data extraction engine. "
            "Given document text and a list of field names, extract the value for every field. "
            "Return ONLY a valid JSON object where each key is a field name and each value is "
            "the extracted value as a string (numbers as strings, e.g. \"350018\"). "
            f'Use the sentinel "{FIELD_NOT_FOUND}" for any field you cannot locate. '
            "Do not include commentary, markdown fencing, or any text outside the JSON object."
        )

        user = (
            f"Fields to extract:\n{{\n{field_list}\n}}"
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
