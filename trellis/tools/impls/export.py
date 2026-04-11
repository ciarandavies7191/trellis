"""export tool — produce a final artifact, with optional schema conformance validation."""

from __future__ import annotations

import json
import logging
import os
import pathlib
import re
import warnings
from typing import Any, Dict, List

from ..base import BaseTool, ToolInput, ToolOutput
from trellis.models.handles import SchemaHandle
from ..decorators import export_io

logger = logging.getLogger(__name__)

DEFAULT_OUTPUT_DIR = os.getenv("TRELLIS_OUTPUT_DIR", "outputs")


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
            )

        out_dir = pathlib.Path(kwargs.get("output_dir", DEFAULT_OUTPUT_DIR))
        written_path = self._write(
            payload=payload,
            fmt=format,
            filename=filename or "artifact",
            out_dir=out_dir,
            schema=schema,
        )

        result: Dict[str, Any] = {
            "status": "success",
            "format": format,
            "filename": filename or "artifact",
            "path": str(written_path),
            "size": written_path.stat().st_size,
        }

        if schema is not None:
            result["schema_source"] = schema.source

        return result

    # ------------------------------------------------------------------
    # Writers
    # ------------------------------------------------------------------

    def _write(
        self,
        payload: Any,
        fmt: str,
        filename: str,
        out_dir: pathlib.Path,
        schema: SchemaHandle | None,
    ) -> pathlib.Path:
        """Write *payload* to *out_dir* and return the path written."""
        out_dir.mkdir(parents=True, exist_ok=True)

        normalised = self._parse_payload(payload)

        if fmt in ("json",):
            return self._write_json(normalised, filename, out_dir)
        # markdown (and anything else) — default writer
        return self._write_markdown(normalised, filename, out_dir, schema)

    @staticmethod
    def _parse_payload(payload: Any) -> Any:
        """
        Normalise payload to a Python object.

        llm_job returns strings (sometimes with markdown code fences). Parse
        JSON strings so downstream writers work with dicts/lists directly.
        """
        if not isinstance(payload, str):
            return payload
        # Strip markdown code fences, e.g. ```json ... ```
        stripped = re.sub(r"^```[a-z]*\s*", "", payload.strip(), flags=re.I)
        stripped = re.sub(r"\s*```$", "", stripped)
        try:
            return json.loads(stripped)
        except Exception:
            return payload  # return as plain string if not JSON

    @staticmethod
    def _write_json(payload: Any, filename: str, out_dir: pathlib.Path) -> pathlib.Path:
        dest = out_dir / f"{filename}.json"
        with open(dest, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False)
        logger.info("export: wrote %s (%d bytes)", dest, dest.stat().st_size)
        return dest

    @staticmethod
    def _write_markdown(
        payload: Any,
        filename: str,
        out_dir: pathlib.Path,
        schema: SchemaHandle | None,
    ) -> pathlib.Path:
        dest = out_dir / f"{filename}.md"
        lines: List[str] = []

        # Header
        lines.append(f"# {filename.replace('_', ' ').title()}")
        lines.append("")

        # Payload is a list of per-period extraction dicts
        if isinstance(payload, list) and payload and isinstance(payload[0], dict):
            periods = list(range(1, len(payload) + 1))
            field_names = schema.field_names() if schema else sorted(payload[0].keys())

            # Column headers
            header = "| Field | " + " | ".join(f"Period {p}" for p in periods) + " |"
            sep = "|---|" + "---|" * len(periods)
            lines.append(header)
            lines.append(sep)

            for field in field_names:
                row_values = []
                for period_dict in payload:
                    val = period_dict.get(field, "__not_found__")
                    row_values.append("—" if val == "__not_found__" else str(val))
                lines.append(f"| {field} | " + " | ".join(row_values) + " |")

        elif isinstance(payload, dict):
            lines.append("| Field | Value |")
            lines.append("|---|---|")
            for k, v in payload.items():
                display = "—" if v == "__not_found__" else str(v)
                lines.append(f"| {k} | {display} |")

        else:
            lines.append(str(payload) if payload is not None else "_no data_")

        dest.write_text("\n".join(lines) + "\n", encoding="utf-8")
        logger.info("export: wrote %s (%d bytes)", dest, dest.stat().st_size)
        return dest

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_and_prepare(
        payload: Any,
        schema: SchemaHandle,
        strict: bool,
    ) -> Any:
        """
        Validate *payload* against *schema* and return the filtered payload.

        Only validates dict payloads; lists and strings pass through unchanged.

        Raises:
            ContractError: if any required field is missing (strict or not).
            ContractError: if strict=True and extra fields are present.
        """
        if not isinstance(payload, dict):
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
            "output_dir": ToolInput(
                name="output_dir",
                description=f"Directory to write output files into (default: TRELLIS_OUTPUT_DIR env var or '{DEFAULT_OUTPUT_DIR}').",
                required=False,
                default=DEFAULT_OUTPUT_DIR,
            ),
        }

    def get_output(self) -> ToolOutput:
        return ToolOutput(
            name="artifact",
            description="Dict with status, format, filename, path (absolute), and size in bytes.",
            type_="object",
        )
