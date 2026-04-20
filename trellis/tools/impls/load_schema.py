"""load_schema tool — produce a SchemaHandle from a file, document, or registry name."""

from __future__ import annotations

import json
import pathlib
from typing import Any, Dict

from ..base import BaseTool, ToolInput, ToolOutput
from trellis.models.handles import FieldDefinition, SchemaHandle


class LoadSchemaTool(BaseTool):
    """
    Produce a SchemaHandle from a schema source.

    Source resolution order:
    1. If a SchemaRegistry is provided and source is a registered name → return directly.
    2. If source is a SchemaHandle already → return as-is (pass-through).
    3. If source is a dict → treat as {field_name: type_hint} or list-of-field-dicts.
    4. If source is a string path/URL → attempt to load and derive schema.
    5. If source is a DocumentHandle-like object → derive from structure.

    In all cases an optional *hint* string guides schema derivation.
    """

    def __init__(self, name: str = "load_schema", schema_registry: Any = None) -> None:
        super().__init__(name, "Load or derive a SchemaHandle from a file, URL, document, or registry name")
        self._schema_registry = schema_registry

    def execute(
        self,
        source: Any,
        hint: str | None = None,
        task_id: str | None = None,
        **kwargs: Any,
    ) -> SchemaHandle:
        """
        Resolve *source* to a SchemaHandle.

        Args:
            source:   File path string, registered schema name, a dict mapping
                      field names to type hints, a list of field definition dicts,
                      an existing SchemaHandle (pass-through), or a DocumentHandle.
            hint:     Optional natural language hint guiding schema derivation
                      (used when source is a DocumentHandle or ambiguous file).
            task_id:  ID of the calling pipeline task, stamped onto the handle.

        Returns:
            A populated SchemaHandle.
        """
        # 1. Already a SchemaHandle — pass through
        if isinstance(source, SchemaHandle):
            return source

        # 2. Registered schema name
        if self._schema_registry is not None and isinstance(source, str):
            if source in self._schema_registry:
                handle = self._schema_registry.get(source)
                if task_id:
                    handle.task_id = task_id
                return handle

        # 3. JSON/YAML file path — load enriched FieldDefinition list
        if isinstance(source, str) and source.lower().endswith((".json", ".yaml", ".yml")):
            p = pathlib.Path(source)
            if p.exists():
                return self._from_schema_file(p, hint=hint, task_id=task_id)

        # 4. Dict source: {field_name: type_hint} or {"fields": [...]}
        if isinstance(source, dict):
            return self._from_dict(source, task_id=task_id)

        # 5. List source: list of field definition dicts
        if isinstance(source, list):
            return self._from_list(source, task_id=task_id)

        # 6. DocumentHandle-like (has .pages or .text attribute) — derive from structure
        if hasattr(source, "pages") or hasattr(source, "text") or hasattr(source, "metadata"):
            return self._from_document(source, hint=hint, task_id=task_id)

        # 7. String: treat as a simple schema name with a single field (fallback)
        if isinstance(source, str):
            return SchemaHandle(
                fields=[],
                source=source,
                raw=source,
                task_id=task_id,
            )

        raise ValueError(
            f"load_schema: cannot derive a SchemaHandle from source of type "
            f"{type(source).__name__!r}. Expected a registered name, dict, list, "
            "DocumentHandle, or existing SchemaHandle."
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _from_dict(self, data: dict, task_id: str | None) -> SchemaHandle:
        """Derive a SchemaHandle from a dict representation."""
        # Support {"fields": [{name, type_hint, required, description}, ...]}
        if "fields" in data and isinstance(data["fields"], list):
            fields = self._from_list(data["fields"], task_id=None)
            return SchemaHandle(
                fields=fields.fields,
                source="dict",
                raw=data,
                task_id=task_id,
            )
        # Support {field_name: type_hint_string, ...}
        fields = [
            FieldDefinition(name=k, type_hint=v if isinstance(v, str) else None)
            for k, v in data.items()
        ]
        return SchemaHandle(fields=fields, source="dict", raw=data, task_id=task_id)

    def _from_schema_file(self, path: pathlib.Path, hint: str | None, task_id: str | None) -> SchemaHandle:
        """Load an enriched SchemaHandle from a JSON (or YAML) schema file.

        Expected format::

            {
              "fields": [
                {
                  "name": "Total Revenues",
                  "type_hint": "number",
                  "section": "face",
                  "description": "...",
                  "sign_convention": null,
                  "computed": false,
                  "formula": null,
                  "manual_ref": "§2.3"
                },
                ...
              ]
            }
        """
        raw_text = path.read_text(encoding="utf-8")
        if path.suffix.lower() in (".yaml", ".yml"):
            try:
                import yaml as _yaml  # type: ignore
                data = _yaml.safe_load(raw_text)
            except ImportError:
                raise ImportError(
                    "PyYAML is required to load .yaml schema files. "
                    "Install with: pip install pyyaml"
                )
        else:
            data = json.loads(raw_text)

        if isinstance(data, dict) and "fields" in data:
            items = data["fields"]
        elif isinstance(data, list):
            items = data
        else:
            items = []

        handle = self._from_list(items, task_id=task_id)
        # Override source with the real file path for traceability
        return SchemaHandle(
            fields=handle.fields,
            source=path.name,
            raw=data,
            task_id=task_id,
        )

    def _from_list(self, items: list, task_id: str | None) -> SchemaHandle:
        """Derive a SchemaHandle from a list of field definition dicts or strings."""
        fields: list[FieldDefinition] = []
        for item in items:
            if isinstance(item, str):
                fields.append(FieldDefinition(name=item))
            elif isinstance(item, dict):
                fields.append(
                    FieldDefinition(
                        name=item.get("name", ""),
                        type_hint=item.get("type_hint") or item.get("type"),
                        required=item.get("required", True),
                        description=item.get("description"),
                        computed=bool(item.get("computed", False)),
                        formula=item.get("formula"),
                        sign_convention=item.get("sign_convention"),
                        section=item.get("section"),
                        cross_check=item.get("cross_check"),
                        fallback_rule=item.get("fallback_rule"),
                        manual_ref=item.get("manual_ref"),
                    )
                )
            elif isinstance(item, FieldDefinition):
                fields.append(item)
        return SchemaHandle(fields=fields, source="list", raw=items, task_id=task_id)

    def _from_document(self, doc: Any, hint: str | None, task_id: str | None) -> SchemaHandle:
        """
        Derive a SchemaHandle from a DocumentHandle.

        Resolution order:
        1. Structured metadata (XLSX/CSV columns from metadata.columns or .headers).
        2. Markdown table first-column row labels (templates use rows as field names).
        3. Empty field list — caller should follow up with an llm_job to populate.
        """
        source_label = getattr(doc, "filename", None) or getattr(doc, "source", "document")

        fields: list[FieldDefinition] = []

        # 1. Structured column metadata (XLSX/CSV)
        columns = None
        if hasattr(doc, "metadata") and isinstance(doc.metadata, dict):
            columns = doc.metadata.get("columns") or doc.metadata.get("headers")
        if hasattr(doc, "columns"):
            columns = doc.columns

        if columns and isinstance(columns, list):
            fields = [
                FieldDefinition(name=str(col), description=hint)
                for col in columns
                if col
            ]
            return SchemaHandle(
                fields=fields,
                source=str(source_label),
                raw=doc,
                task_id=task_id,
            )

        # 2. Markdown table row labels: concatenate page text and parse tables
        if not fields:
            full_text = ""
            if hasattr(doc, "pages") and isinstance(doc.pages, list):
                full_text = "\n".join(
                    (getattr(p, "text", None) or "") for p in doc.pages
                )
            elif hasattr(doc, "text"):
                full_text = doc.text or ""

            if full_text:
                fields = self._fields_from_markdown(full_text, hint=hint)

        return SchemaHandle(
            fields=fields,
            source=str(source_label),
            raw=doc,
            task_id=task_id,
        )

    @staticmethod
    def _fields_from_markdown(text: str, hint: str | None) -> list[FieldDefinition]:
        """
        Extract field names from Markdown table first-column row labels.

        Markdown financial templates use a layout where the first column lists
        metric names (e.g. "Total Revenues", "Cost of Revenues") and subsequent
        columns are periods.  This method collects those row labels as fields.

        Rows that are separators (|----|) or blank first cells are skipped.
        Markdown emphasis markers (* ** _ __) are stripped from labels.
        """
        import re

        fields: list[FieldDefinition] = []
        seen: set[str] = set()

        for line in text.splitlines():
            line = line.strip()
            if not line.startswith("|"):
                continue
            # Split keeping all cells so positional meaning is preserved.
            # A Markdown table row looks like: | label | val | val |
            # Split on | gives: ["", "label", "val", "val", ""]
            raw_cells = line.split("|")
            # Index 1 is the first column; skip if blank (period-header rows
            # like "| | Q1 20__ | ..." have an intentionally empty first column)
            if len(raw_cells) < 2 or not raw_cells[1].strip():
                continue
            first_raw = raw_cells[1].strip()
            # Skip separator rows like |---|---:|
            if re.fullmatch(r"[-:| ]+", first_raw):
                continue
            # Strip Markdown emphasis/bold markers (* ** _ __)
            label = re.sub(r"[*_]+", "", first_raw).strip()
            # Skip blank, purely numeric (footnote numbers), or single-char labels
            if not label or re.fullmatch(r"[\d\s#*]+", label) or len(label) < 3:
                continue
            if label not in seen:
                seen.add(label)
                fields.append(FieldDefinition(name=label, description=hint))

        return fields

    def get_inputs(self) -> Dict[str, ToolInput]:
        return {
            "source": ToolInput(
                name="source",
                description=(
                    "Schema source: registered name, file path, dict of field definitions, "
                    "list of field names, DocumentHandle, or existing SchemaHandle."
                ),
                required=True,
            ),
            "hint": ToolInput(
                name="hint",
                description=(
                    "Optional natural language hint to guide schema derivation "
                    "when source is a DocumentHandle or ambiguous file."
                ),
                required=False,
                default=None,
            ),
        }

    def get_output(self) -> ToolOutput:
        return ToolOutput(
            name="schema",
            description="A SchemaHandle carrying field definitions and source provenance.",
            type_="SchemaHandle",
        )
