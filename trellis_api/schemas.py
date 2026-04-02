"""API schemas for Trellis FastAPI server."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from trellis.models.pipeline import Pipeline
from trellis.models.plan import Plan


class ExecutionOptionsIn(BaseModel):
    """Subset of execution options controllable via API."""

    per_task_timeout: float | None = Field(default=None, ge=0, description="Per-attempt timeout in seconds")
    fan_out_concurrency: int | None = Field(default=None, ge=1, description="Max parallel fan-out")
    backoff_jitter: float = Field(default=0.0, ge=0.0, le=1.0, description="Jitter fraction (0.2 = ±20%)")


class PipelineRunRequest(BaseModel):
    """Request body to run a pipeline."""

    pipeline: Pipeline
    inputs: Dict[str, Any] | None = None
    session: Dict[str, Any] | None = None
    options: ExecutionOptionsIn | None = None
    collect_events: bool = Field(default=False)


class PipelineRunResponse(BaseModel):
    """Response body from running a pipeline."""

    outputs: Dict[str, Any]
    waves_executed: int
    tasks_executed: int
    events: List[Dict[str, Any]] | None = None


class ValidateResponse(BaseModel):
    ok: bool
    errors: List[str] | None = None
    message: str | None = None


class ToolListResponse(BaseModel):
    tools: List[str]


class PlanValidateRequest(BaseModel):
    plan: Plan


class PlanValidateResponse(BaseModel):
    ok: bool
    errors: List[str] | None = None
    message: str | None = None

