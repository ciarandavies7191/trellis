"""Blackboard pattern for shared execution context."""

from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field


@dataclass
class BlackboardEntry:
    """Entry in the blackboard."""

    key: str
    value: Any
    source_task: Optional[str] = None
    timestamp: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


class Blackboard:
    """Shared execution context for task communication."""

    def __init__(self):
        """Initialize blackboard."""
        self.entries: Dict[str, BlackboardEntry] = {}
        self.history: List[Dict[str, Any]] = []

    def write(
        self,
        key: str,
        value: Any,
        source_task: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        Write value to blackboard.

        Args:
            key: Key to write to
            value: Value to store
            source_task: Task ID that produced this value
            metadata: Additional metadata
        """
        entry = BlackboardEntry(
            key=key,
            value=value,
            source_task=source_task,
            metadata=metadata or {}
        )
        self.entries[key] = entry
        self.history.append({"action": "write", "key": key, "source": source_task})

    def read(self, key: str) -> Optional[Any]:
        """
        Read value from blackboard.

        Args:
            key: Key to read

        Returns:
            Value or None if not found
        """
        if key in self.entries:
            return self.entries[key].value
        return None

    def exists(self, key: str) -> bool:
        """Check if key exists in blackboard."""
        return key in self.entries

    def get_all(self) -> Dict[str, Any]:
        """Get all values on blackboard."""
        return {k: v.value for k, v in self.entries.items()}

    def clear(self) -> None:
        """Clear all values."""
        self.entries.clear()
        self.history.append({"action": "clear"})

    def get_history(self) -> List[Dict[str, Any]]:
        """Get blackboard access history."""
        return self.history.copy()
