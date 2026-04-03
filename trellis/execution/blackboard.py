"""
Simple tenant-scoped blackboard for session persistence.

This module provides a minimal in-memory implementation suitable for local
runs and testing. The interface is intentionally tiny so it can be swapped for
Redis/Postgres/Prefect Blocks later without touching the executor.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable


class Blackboard:
    """Abstract tenant-scoped key/value store."""

    def get_all(self, tenant_id: str) -> Dict[str, Any]:  # pragma: no cover - interface
        raise NotImplementedError

    def read_many(self, tenant_id: str, keys: Iterable[str]) -> Dict[str, Any]:  # pragma: no cover - interface
        raise NotImplementedError

    def write(self, tenant_id: str, key: str, value: Any, *, append: bool = False) -> None:  # pragma: no cover - interface
        raise NotImplementedError


@dataclass
class InMemoryBlackboard(Blackboard):
    """Naive in-memory implementation.

    Data layout: { tenant_id -> { key -> value } }
    """

    _data: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    def _tenant(self, tenant_id: str) -> Dict[str, Any]:
        return self._data.setdefault(tenant_id, {})

    def get_all(self, tenant_id: str) -> Dict[str, Any]:
        return dict(self._tenant(tenant_id))

    def read_many(self, tenant_id: str, keys: Iterable[str]) -> Dict[str, Any]:
        t = self._tenant(tenant_id)
        return {k: t[k] for k in keys if k in t}

    def write(self, tenant_id: str, key: str, value: Any, *, append: bool = False) -> None:
        t = self._tenant(tenant_id)
        if append:
            if key not in t:
                t[key] = [value]
            else:
                existing = t[key]
                if isinstance(existing, list):
                    existing.append(value)
                else:
                    t[key] = [existing, value]
        else:
            t[key] = value

