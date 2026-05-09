from __future__ import annotations

import re
from typing import Optional

try:
    from dateutil import parser as dateutil_parser  # type: ignore
    _DATEUTIL_AVAILABLE = True
except ImportError:
    _DATEUTIL_AVAILABLE = False

from .models import ChunkMetadata
from .plugins import RetrievalRegistry

PERIOD_PATTERNS = [
    r"(?:three|six|nine|twelve)\s+months?\s+ended\s+(\w+\s+\d{1,2},?\s+\d{4})",
    r"(?:year|quarter|period)\s+ended\s+(\w+\s+\d{1,2},?\s+\d{4})",
    r"(Q[1-4]\s+(?:FY|CY)?\d{4})",
    r"(\d{4}-\d{2}-\d{2})",
    r"(\d{1,2}/\d{1,2}/\d{2,4})",
]


def _extract_periods(text: str) -> tuple[list[str], list[str]]:
    """Returns (period_labels, period_ends_iso)."""
    labels: list[str] = []
    iso_dates: list[str] = []
    seen: set[str] = set()
    for pat in PERIOD_PATTERNS:
        for m in re.finditer(pat, text, re.IGNORECASE):
            raw = m.group(1)
            if raw in seen:
                continue
            seen.add(raw)
            labels.append(raw)
            if _DATEUTIL_AVAILABLE:
                try:
                    dt = dateutil_parser.parse(raw, fuzzy=True)
                    iso_dates.append(dt.strftime("%Y-%m-%d"))
                except Exception:
                    iso_dates.append(raw)
            else:
                iso_dates.append(raw)
    return labels, iso_dates


class MetadataExtractor:
    def extract(self, chunks: list[ChunkMetadata], registry: RetrievalRegistry) -> None:
        """Mutates chunks in-place."""
        for chunk in chunks:
            chunk.section_label = registry.classifier.classify(chunk)
            combined = chunk.text + " " + " ".join(chunk.column_labels)
            labels, iso_dates = _extract_periods(combined)
            chunk.period_labels = labels
            chunk.period_ends = iso_dates
