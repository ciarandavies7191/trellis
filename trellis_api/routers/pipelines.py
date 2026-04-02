from __future__ import annotations

from fastapi import APIRouter, HTTPException

from trellis_api.schemas import (
    PipelineRunRequest,
    PipelineRunResponse,
    ValidateResponse,
    ToolListResponse,
)
from trellis.models.pipeline import Pipeline
from trellis.execution.orchestrator import Orchestrator
from trellis.execution.dag import ExecutionOptions
from trellis.tools.registry import build_default_registry

router = APIRouter()


@router.post("/validate", response_model=ValidateResponse)
def validate_pipeline(req: PipelineRunRequest) -> ValidateResponse:
    try:
        # Validates by re-constructing Pipeline via pydantic
        Pipeline.model_validate(req.pipeline.model_dump())
        return ValidateResponse(ok=True, message="Pipeline is valid")
    except Exception as exc:  # noqa: BLE001
        return ValidateResponse(ok=False, errors=[str(exc)], message="Validation failed")


@router.get("/tools", response_model=ToolListResponse)
def list_tools() -> ToolListResponse:
    reg = build_default_registry()
    return ToolListResponse(tools=reg.registered_tools())


@router.post("/run", response_model=PipelineRunResponse)
async def run_pipeline(req: PipelineRunRequest) -> PipelineRunResponse:
    try:
        # Validate/coerce the incoming Pipeline
        pipeline = Pipeline.model_validate(req.pipeline.model_dump())
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Invalid pipeline: {exc}")

    options = ExecutionOptions(
        per_task_timeout=req.options.per_task_timeout if req.options else None,
        fan_out_concurrency=req.options.fan_out_concurrency if req.options else None,
        backoff_jitter=req.options.backoff_jitter if req.options else 0.0,
    )

    orch = Orchestrator()
    result = await orch.run_pipeline(
        pipeline,
        inputs=req.inputs,
        session=req.session,
        options=options,
        collect_events=req.collect_events,
    )

    return PipelineRunResponse(
        outputs=result.outputs,
        waves_executed=result.waves_executed,
        tasks_executed=result.tasks_executed,
        events=result.events if req.collect_events else None,
    )

