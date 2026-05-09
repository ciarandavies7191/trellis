from __future__ import annotations

import re
import uuid
from typing import Any

from trellis.models.document import DocumentHandle
from .models import ChunkMetadata, ChunkType

_MAX_PROSE_TOKENS = 512


def _approx_tokens(text: str) -> int:
    return max(1, len(text.split()) * 4 // 3)


def _is_table_block(lines: list[str]) -> bool:
    if not lines:
        return False
    pipe_count = sum(1 for l in lines if "|" in l)
    if pipe_count / len(lines) >= 0.5:
        return True
    csv_count = sum(1 for l in lines if len(re.split(r" {2,}", l.strip())) >= 3)
    return csv_count / len(lines) >= 0.5


def _split_table_row(line: str) -> list[str]:
    if "|" in line:
        return [c.strip() for c in line.split("|") if c.strip()]
    return [c.strip() for c in re.split(r" {2,}", line.strip()) if c.strip()]


def _extract_table_labels(lines: list[str]) -> tuple[list[str], list[str]]:
    non_empty = [l for l in lines if l.strip()]
    if not non_empty:
        return [], []
    column_labels = _split_table_row(non_empty[0])
    row_labels = []
    for line in non_empty[1:]:
        parts = _split_table_row(line)
        if parts:
            row_labels.append(parts[0])
    return column_labels, row_labels


def _is_heading(line: str, in_table: bool) -> bool:
    stripped = line.strip()
    if not stripped or in_table:
        return False
    if len(stripped) > 80:
        return False
    if stripped.isupper() and len(stripped) > 1:
        return True
    words = stripped.split()
    if len(words) >= 2 and stripped == stripped.title():
        return True
    if re.match(r"^\d+(\.\d+)*\.?\s+\S", stripped):
        return True
    if stripped.endswith(":") and len(stripped) <= 50:
        return True
    return False


def _is_footnote_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    if stripped[0] in ("*", "†", "‡", "§"):
        return True
    if re.match(r"^\^?\d+[\.\)]", stripped):
        return True
    if re.match(r"^\[\d+\]", stripped):
        return True
    return False


def _split_prose(text: str) -> list[str]:
    if _approx_tokens(text) <= _MAX_PROSE_TOKENS:
        return [text]
    sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z])", text)
    chunks: list[str] = []
    current: list[str] = []
    current_tokens = 0
    for sentence in sentences:
        t = _approx_tokens(sentence)
        if current_tokens + t > _MAX_PROSE_TOKENS and current:
            chunks.append(" ".join(current))
            current = [sentence]
            current_tokens = t
        else:
            current.append(sentence)
            current_tokens += t
    if current:
        chunks.append(" ".join(current))
    return chunks


def _make_chunk(
    document_id: str,
    tenant_id: str,
    page_number: int,
    chunk_type: ChunkType,
    text: str,
    column_labels: list[str] | None = None,
    row_labels: list[str] | None = None,
    extra: dict[str, Any] | None = None,
) -> ChunkMetadata:
    return ChunkMetadata(
        chunk_id=str(uuid.uuid4()),
        document_id=document_id,
        tenant_id=tenant_id,
        page=page_number,
        chunk_type=chunk_type,
        section_label=None,
        period_labels=[],
        period_ends=[],
        row_labels=row_labels or [],
        column_labels=column_labels or [],
        extra=extra or {},
        text=text,
        embedding=None,
    )


class StructuralChunker:
    def chunk(self, handle: DocumentHandle, document_id: str, tenant_id: str) -> list[ChunkMetadata]:
        result: list[ChunkMetadata] = []
        for page in handle.pages:
            if page.sheet_name is not None:
                col_labels, row_labels = _extract_table_labels(page.text.splitlines())
                result.append(_make_chunk(
                    document_id, tenant_id, page.number,
                    ChunkType.TABLE, page.text,
                    column_labels=col_labels,
                    row_labels=row_labels,
                    extra={"sheet_name": page.sheet_name},
                ))
                continue

            lines = page.text.splitlines()
            result.extend(_parse_lines(lines, document_id, tenant_id, page.number))
        return result


def _parse_lines(
    lines: list[str],
    document_id: str,
    tenant_id: str,
    page_number: int,
) -> list[ChunkMetadata]:
    result: list[ChunkMetadata] = []
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]

        # --- footnote ---
        if _is_footnote_line(line):
            block = [line]
            j = i + 1
            while j < n and _is_footnote_line(lines[j]):
                block.append(lines[j])
                j += 1
            result.append(_make_chunk(
                document_id, tenant_id, page_number,
                ChunkType.FOOTNOTE, "\n".join(block),
            ))
            i = j
            continue

        # --- table: lookahead to collect block ---
        lookahead: list[str] = []
        j = i
        while j < n and lines[j].strip():
            lookahead.append(lines[j])
            j += 1
        if lookahead and _is_table_block(lookahead):
            col_labels, row_labels = _extract_table_labels(lookahead)
            result.append(_make_chunk(
                document_id, tenant_id, page_number,
                ChunkType.TABLE, "\n".join(lookahead),
                column_labels=col_labels,
                row_labels=row_labels,
            ))
            i = j
            continue

        # --- heading ---
        if line.strip() and _is_heading(line, in_table=False):
            result.append(_make_chunk(
                document_id, tenant_id, page_number,
                ChunkType.HEADING, line.strip(),
            ))
            i += 1
            continue

        # --- blank line: skip ---
        if not line.strip():
            i += 1
            continue

        # --- prose: collect paragraph ---
        para_lines: list[str] = []
        while i < n and lines[i].strip():
            para_lines.append(lines[i])
            i += 1
        para_text = " ".join(para_lines)
        for prose_chunk in _split_prose(para_text):
            if prose_chunk.strip():
                result.append(_make_chunk(
                    document_id, tenant_id, page_number,
                    ChunkType.PROSE, prose_chunk.strip(),
                ))

    return result
