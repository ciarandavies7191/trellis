"""load_schema tool — produce a SchemaHandle from a file, document, or registry name."""

from __future__ import annotations

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

        # 3. Dict source: {field_name: type_hint} or {"fields": [...]}
        if isinstance(source, dict):
            return self._from_dict(source, task_id=task_id)

        # 4. List source: list of field definition dicts
        if isinstance(source, list):
            return self._from_list(source, task_id=task_id)

        # 5. DocumentHandle-like (has .pages or .text attribute) — derive from structure
        if hasattr(source, "pages") or hasattr(source, "text") or hasattr(source, "metadata"):
            return self._from_document(source, hint=hint, task_id=task_id)

        # 6. String: treat as a simple schema name with a single field (fallback)
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
                    )
                )
            elif isinstance(item, FieldDefinition):
                fields.append(item)
        return SchemaHandle(fields=fields, source="list", raw=items, task_id=task_id)

    def _from_document(self, doc: Any, hint: str | None, task_id: str | None) -> SchemaHandle:
        """
        Derive a SchemaHandle from a DocumentHandle.

        For structured documents (XLSX, CSV, JSON), column/key names become
        field definitions. For unstructured documents, *hint* is required and
        the field list is empty (a downstream llm_job should populate it).
        """
        source_label = getattr(doc, "filename", None) or getattr(doc, "source", "document")

        # Try to extract field names from document metadata or content
        fields: list[FieldDefinition] = []

        # Check for column metadata (XLSX/CSV documents typically populate this)
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
