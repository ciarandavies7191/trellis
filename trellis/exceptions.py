"""
trellis.exceptions — Shared exception hierarchy for the Trellis runtime.

All public-facing errors inherit from TrellisError so callers can catch
broadly or narrowly as needed.
"""

from __future__ import annotations


class TrellisError(Exception):
    """Base class for all Trellis errors."""


# ---------------------------------------------------------------------------
# Validation errors
# ---------------------------------------------------------------------------


class ValidationError(TrellisError):
    """Base class for all validation failures."""


class CycleError(ValidationError):
    """
    Raised when a cycle is detected in a task DAG or a plan sub-pipeline DAG.

    Attributes:
        cycle: The sequence of node IDs forming the cycle, if recoverable.
               May be None if only the presence of a cycle is known.
    """

    def __init__(self, message: str, cycle: list[str] | None = None) -> None:
        super().__init__(message)
        self.cycle = cycle


class UnresolvedRefError(ValidationError):
    """
    Raised when a template expression references a task or key that does
    not exist in the current scope.
    """


class ContractError(ValidationError):
    """
    Raised when a pipeline's store tasks do not match the sub-pipeline's
    stores declaration in the plan, or when a session reference is made
    to a key not listed in reads.
    """


class UnknownToolError(ValidationError):
    """Raised when a task references a tool not present in the registry."""


# ---------------------------------------------------------------------------
# Execution errors
# ---------------------------------------------------------------------------


class ExecutionError(TrellisError):
    """Base class for runtime execution failures."""


class ResolutionError(ExecutionError):
    """
    Raised when a template expression cannot be resolved against the current
    execution context — e.g. a task output that does not exist yet, a
    field path that does not match the output structure, or an unknown
    namespace.
    """


class TaskFailedError(ExecutionError):
    """
    Raised when a task fails after exhausting all retry attempts.

    Attributes:
        task_id: The id of the failing task.
        cause:   The underlying exception that caused the failure.
    """

    def __init__(self, task_id: str, cause: Exception) -> None:
        super().__init__(f"Task {task_id!r} failed: {cause}")
        self.task_id = task_id
        self.cause = cause


class BlackboardKeyError(ExecutionError):
    """
    Raised when a session blackboard read is attempted for a key that does
    not exist and no default is provided.
    """