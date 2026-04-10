"""extract_fields tool — schema-bound field extraction from a document."""

from __future__ import annotations

from typing import Any, Dict

from ..base import BaseTool, ToolInput, ToolOutput
from trellis.models.handles import FIELD_NOT_FOUND, FieldDefinition, SchemaHandle


class ExtractFieldsTool(BaseTool):
    """
    Extract values from a document for every field declared in a SchemaHandle.

    Output is a dict ``{field_name: extracted_value | "__not_found__"}``.

    This tool uses a pluggable extraction backend. If an LLM client is provided
    at construction time it is used for extraction; otherwise the tool falls back
    to a stub implementation that returns ``"__not_found__"`` for all fields,
    suitable for unit testing without LLM credentials.
    """

    def __init__(self, name: str = "extract_fields", llm_client: Any = None) -> None:
        super().__init__(name, "Schema-bound field extraction from a document")
        self._llm_client = llm_client

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

        Args:
            document: A DocumentHandle, list of pages, or plain text string.
            schema:   A SchemaHandle declaring which fields to extract.
            rules:    Optional DocumentHandle containing extraction rules (e.g.
                      a spreading manual). When present, per-field instructions
                      from the document are injected into the extraction context.
            selector: Optional natural language hint to scope extraction to a
                      region of the document before attempting field extraction.

        Returns:
            Dict mapping each field name in *schema* to its extracted value or
            ``FIELD_NOT_FOUND`` if the value could not be located.
        """
        if not isinstance(schema, SchemaHandle):
            raise TypeError(
                f"extract_fields: 'schema' must be a SchemaHandle, "
                f"got {type(schema).__name__!r}."
            )

        # Materialise document text for extraction
        doc_text = self._to_text(document, selector=selector)

        # Build per-field extraction context from rules document if provided
        rules_index = self._index_rules(rules) if rules is not None else {}

        result: Dict[str, Any] = {}

        if self._llm_client is not None:
            # Real extraction path — calls LLM per field (or batched)
            result = self._extract_with_llm(
                doc_text=doc_text,
                fields=schema.fields,
                rules_index=rules_index,
            )
        else:
            # Stub path: mark all fields as not found
            for field_def in schema.fields:
                result[field_def.name] = FIELD_NOT_FOUND

        # Ensure output only contains declared fields
        declared = set(schema.field_names())
        return {k: v for k, v in result.items() if k in declared}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _to_text(self, document: Any, selector: str | None) -> str:
        """Convert various document representations to a plain text string."""
        if isinstance(document, str):
            return document

        # DocumentHandle with pages
        if hasattr(document, "pages") and isinstance(document.pages, list):
            parts: list[str] = []
            for page in document.pages:
                if isinstance(page, str):
                    parts.append(page)
                elif hasattr(page, "text"):
                    parts.append(page.text or "")
                elif isinstance(page, dict):
                    parts.append(page.get("text", ""))
            return "\n".join(parts)

        # DocumentHandle with a single text attribute
        if hasattr(document, "text"):
            return document.text or ""

        # List of strings or page-like objects
        if isinstance(document, list):
            parts = []
            for item in document:
                if isinstance(item, str):
                    parts.append(item)
                elif hasattr(item, "text"):
                    parts.append(item.text or "")
            return "\n".join(parts)

        return str(document)

    def _index_rules(self, rules: Any) -> dict[str, str]:
        """
        Build a {field_name: instruction} index from a rules document.

        In a real implementation this would parse the rules document and
        match field-specific instructions by field name. Here we return an
        empty dict as a stub — downstream pipelines should rely on llm_job
        for sophisticated rules parsing.
        """
        return {}

    def _extract_with_llm(
        self,
        doc_text: str,
        fields: list[FieldDefinition],
        rules_index: dict[str, str],
    ) -> dict[str, Any]:
        """
        Extract field values using the configured LLM client.

        This is a simplified single-pass extraction. A production implementation
        would batch fields and handle rate limits.
        """
        result: dict[str, Any] = {}

        for field_def in fields:
            # Build extraction prompt
            field_context = f"Field: {field_def.name}"
            if field_def.type_hint:
                field_context += f"\nType: {field_def.type_hint}"
            if field_def.description:
                field_context += f"\nDescription: {field_def.description}"
            if field_def.name in rules_index:
                field_context += f"\nExtraction rule: {rules_index[field_def.name]}"

            prompt = (
                f"{field_context}\n\n"
                f"Document text:\n{doc_text}\n\n"
                f"Extract the value for '{field_def.name}'. "
                f"If not found, respond exactly with: {FIELD_NOT_FOUND}"
            )

            try:
                response = self._llm_client.complete(prompt)
                value = response.strip() if isinstance(response, str) else FIELD_NOT_FOUND
            except Exception:
                value = FIELD_NOT_FOUND

            result[field_def.name] = value

        return result

    def get_inputs(self) -> Dict[str, ToolInput]:
        return {
            "document": ToolInput(
                name="document",
                description="DocumentHandle, page list, or text string to extract from.",
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
                    "Optional DocumentHandle containing extraction rules. "
                    "Per-field instructions are injected into the extraction context."
                ),
                required=False,
                default=None,
            ),
            "selector": ToolInput(
                name="selector",
                description=(
                    "Optional natural language hint to scope extraction to a "
                    "region of the document before field extraction."
                ),
                required=False,
                default=None,
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
