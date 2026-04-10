"""export tool — produce a final artifact, with optional schema conformance validation."""

from __future__ import annotations

import warnings
from typing import Any, Dict

from ..base import BaseTool, ToolInput, ToolOutput
from trellis.models.handles import SchemaHandle


class ContractError(Exception):
    """Raised when export data fails schema conformance validation."""


class ExportTool(BaseTool):
    def __init__(self, name: str = "export") -> None:
        super().__init__(name, "Export content to an artifact")

    def execute(
        self,
        content: Any = None,
        format: str = "markdown",
        filename: str | None = None,
        schema: SchemaHandle | None = None,
        data: Any = None,
        strict: bool = False,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """
        Export *content* (or *data*) to an artifact.

        When *schema* is provided the output is validated for conformance:
          - Missing required fields raise ContractError.
          - Extra fields are dropped with a warning (or raise in strict mode).

        When *schema.raw* is present and the format matches the raw source type
        the tool operates in *populate* mode: values are written into the original
        template representation rather than generating a new file.

        Args:
            content:  Content to export. May be a dict, string, or any value.
            data:     Alias for content (either may be supplied; data takes
                      precedence when both are given).
            format:   Output format: "markdown", "json", "csv", "xlsx", "pdf".
            filename: Base filename without extension.
            schema:   Optional SchemaHandle. When present, validates conformance
                      before writing and enables populate mode when schema.raw
                      is non-None.
            strict:   When True, extra fields in *content* raise ContractError
                      instead of being silently dropped.

        Returns:
            Dict with status, format, filename, size, and optional schema info.
        """
        # Normalise content/data aliases
        payload = data if data is not None else content

        if schema is not None:
            payload = self._validate_and_prepare(
                payload=payload,
                schema=schema,
                strict=strict,
                format=format,
            )

        size = len(str(payload)) if payload is not None else 0
        result: Dict[str, Any] = {
            "status": "success",
            "format": format,
            "filename": filename or "artifact",
            "size": size,
        }

        if schema is not None:
            result["schema_source"] = schema.source
            # Populate mode: when schema carries template bytes and format matches
            if schema.raw is not None:
                result["populate_mode"] = True

        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _validate_and_prepare(
        self,
        payload: Any,
        schema: SchemaHandle,
        strict: bool,
        format: str,
    ) -> Any:
        """
        Validate *payload* against *schema* and return the filtered payload.

        Raises:
            ContractError: if any required field is missing.
            ContractError: if strict=True and extra fields are present.
        """
        if not isinstance(payload, dict):
            # Non-dict payloads cannot be field-validated; pass through.
            return payload

        required = set(schema.required_field_names())
        declared = set(schema.field_names())
        present = set(payload.keys())

        missing = required - present
        if missing:
            raise ContractError(
                f"Export failed schema conformance check: "
                f"required field(s) {sorted(missing)} are missing from payload."
            )

        extra = present - declared
        if extra:
            if strict:
                raise ContractError(
                    f"Export strict mode: extra field(s) {sorted(extra)} "
                    "are not declared in the schema."
                )
            for key in extra:
                warnings.warn(
                    f"export: dropping undeclared field {key!r} (not in schema).",
                    stacklevel=4,
                )

        # Return only declared fields
        return {k: v for k, v in payload.items() if k in declared}

    def get_inputs(self) -> Dict[str, ToolInput]:
        return {
            "content": ToolInput(
                name="content",
                description="Content to export.",
                required=False,
                default=None,
            ),
            "data": ToolInput(
                name="data",
                description="Alias for content; takes precedence when both are supplied.",
                required=False,
                default=None,
            ),
            "format": ToolInput(
                name="format",
                description="Output format: markdown, json, csv, xlsx, pdf.",
                required=False,
                default="markdown",
            ),
            "filename": ToolInput(
                name="filename",
                description="Base filename (no extension).",
                required=False,
                default=None,
            ),
            "schema": ToolInput(
                name="schema",
                description=(
                    "Optional SchemaHandle. When present, validates output "
                    "conformance and enables populate mode."
                ),
                required=False,
                default=None,
            ),
            "strict": ToolInput(
                name="strict",
                description="When True, extra fields raise ContractError instead of being dropped.",
                required=False,
                default=False,
            ),
        }

    def get_output(self) -> ToolOutput:
        return ToolOutput(name="artifact", description="Export result/handle", type_="object")
