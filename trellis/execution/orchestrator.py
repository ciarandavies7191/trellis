"""Orchestrator for coordinating pipeline execution."""

from typing import Dict, Any, Optional, List
from enum import Enum
from dataclasses import dataclass, field
from datetime import datetime


class PipelineState(str, Enum):
    """Pipeline execution state."""
    CREATED = "created"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class ExecutionMetrics:
    """Execution metrics and statistics."""

    total_tasks: int = 0
    completed_tasks: int = 0
    failed_tasks: int = 0
    skipped_tasks: int = 0
    total_duration: float = 0.0
    tasks_duration: Dict[str, float] = field(default_factory=dict)
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None


class Orchestrator:
    """Orchestrates pipeline execution with monitoring."""

    def __init__(self):
        """Initialize orchestrator."""
        self.state = PipelineState.CREATED
        self.metrics = ExecutionMetrics()
        self.execution_log: List[Dict[str, Any]] = []

    def start_pipeline(self, pipeline_id: str) -> None:
        """Start pipeline execution."""
        self.state = PipelineState.RUNNING
        self.metrics.start_time = datetime.now()
        self._log_event("PIPELINE_STARTED", {"pipeline_id": pipeline_id})

    def complete_task(self, task_id: str, success: bool, duration: float) -> None:
        """Log task completion."""
        self.metrics.completed_tasks += 1
        self.metrics.tasks_duration[task_id] = duration

        if success:
            event_type = "TASK_SUCCESS"
        else:
            self.metrics.failed_tasks += 1
            event_type = "TASK_FAILED"

        self._log_event(event_type, {"task_id": task_id, "duration": duration})

    def end_pipeline(self, success: bool) -> None:
        """End pipeline execution."""
        self.metrics.end_time = datetime.now()

        if self.metrics.start_time:
            self.metrics.total_duration = (
                self.metrics.end_time - self.metrics.start_time
            ).total_seconds()

        self.state = PipelineState.SUCCESS if success else PipelineState.FAILED
        self._log_event("PIPELINE_ENDED", {
            "state": self.state,
            "duration": self.metrics.total_duration
        })

    def cancel_pipeline(self) -> None:
        """Cancel pipeline execution."""
        self.state = PipelineState.CANCELLED
        self.metrics.end_time = datetime.now()
        self._log_event("PIPELINE_CANCELLED", {})

    def _log_event(self, event_type: str, data: Dict[str, Any]) -> None:
        """Log an execution event."""
        self.execution_log.append({
            "timestamp": datetime.now().isoformat(),
            "type": event_type,
            "data": data
        })

    def get_status(self) -> Dict[str, Any]:
        """Get current pipeline status."""
        return {
            "state": self.state,
            "metrics": {
                "total_tasks": self.metrics.total_tasks,
                "completed_tasks": self.metrics.completed_tasks,
                "failed_tasks": self.metrics.failed_tasks,
                "skipped_tasks": self.metrics.skipped_tasks,
                "total_duration": self.metrics.total_duration
            },
            "start_time": self.metrics.start_time.isoformat() if self.metrics.start_time else None,
            "end_time": self.metrics.end_time.isoformat() if self.metrics.end_time else None
        }
