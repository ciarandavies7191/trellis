from __future__ import annotations

from fastapi import APIRouter, HTTPException
import dataclasses
from typing import Any

from trellis_api.schemas import (
    PipelineRunRequest,
    PipelineRunResponse,
    ValidateResponse,
    ToolListResponse,
    QueuedRunRequest,
    QueuedRunResponse,
    RunStatusResponse,
)
from trellis.models.pipeline import Pipeline
from trellis.execution.orchestrator import Orchestrator
from trellis.execution.dag import ExecutionOptions, TaskError
from trellis.execution.run_queue import run_manager
from trellis.tools.registry import build_default_registry
from trellis.tools.base import BaseTool, ToolInput, ToolOutput

router = APIRouter()


def _json_sanitize(value: Any) -> Any:
    """
    Recursively convert outputs to JSON-serializable structures.

    - dataclasses → dict
    - bytes       → placeholder dict with size to avoid large payloads
    - sets/tuples → lists
    - unknown objects → str(value)
    """
    # Bytes: avoid utf-8 decoding errors and huge payloads
    if isinstance(value, (bytes, bytearray)):
        return {"__bytes__": True, "size": len(value)}

    # Dataclasses: convert to dict, then recurse
    if dataclasses.is_dataclass(value):
        try:
            value = dataclasses.asdict(value)
        except Exception:
            value = dict(value.__dict__)  # type: ignore[attr-defined]

    # Dicts: sanitize values
    if isinstance(value, dict):
        return {k: _json_sanitize(v) for k, v in value.items()}

    # Lists/Tuples/Sets: to list and sanitize
    if isinstance(value, (list, tuple, set)):
        return [_json_sanitize(v) for v in list(value)]

    # Simple scalars are fine
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value

    # Fallback to string representation
    try:
        return str(value)
    except Exception:
        return repr(value)


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
    names = reg.registered_tools()

    # Build metadata by instantiating the tool classes already in registry
    metadata = []
    for name in names:
        # Access the underlying tool object if available via registry internals
        tool_obj: BaseTool | None = getattr(reg, "_tools_by_name", {}).get(name)  # type: ignore[attr-defined]
        if tool_obj is None:
            # As a fallback, try importing metadata via get_tool_metadata pattern
            try:
                # Best-effort: skip if not a BaseTool-backed entry
                continue
            except Exception:
                continue
        # Inputs schema to plain dicts
        inputs_spec = {}
        try:
            for key, spec in tool_obj.get_inputs().items():
                inputs_spec[key] = {
                    "name": spec.name,
                    "description": spec.description,
                    "required": spec.required,
                    "default": spec.default,
                }
        except Exception:
            inputs_spec = {}
        # Output spec to dict
        try:
            out = tool_obj.get_output()
            output_spec = {
                "name": out.name,
                "description": out.description,
                "type": getattr(out, "type_", "object"),
            }
        except Exception:
            output_spec = {"name": "output", "description": "Tool output", "type": "object"}

        metadata.append({
            "name": tool_obj.name,
            "description": tool_obj.description,
            "inputs": inputs_spec,
            "output": output_spec,
        })

    return ToolListResponse(tools=names, metadata=metadata)


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
    try:
        result = await orch.run_pipeline(
            pipeline,
            inputs=req.inputs,
            session=req.session,
            options=options,
            collect_events=req.collect_events,
        )
    except TaskError as exc:
        # If the underlying cause is a RuntimeError (e.g., provider/model misconfiguration),
        # return a 400 with a clear message instead of a generic 500.
        cause = getattr(exc, "cause", None)
        if isinstance(cause, RuntimeError):
            raise HTTPException(status_code=400, detail=str(cause)) from exc
        # Otherwise treat as server error
        raise HTTPException(status_code=500, detail=f"Task {exc.task_id} failed: {cause}") from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Execution failed: {exc}") from exc

    # Sanitize non-JSON-serializable values (e.g., bytes in DocumentHandle pages)
    safe_outputs = _json_sanitize(result.outputs)
    safe_events = _json_sanitize(result.events) if req.collect_events else None

    return PipelineRunResponse(
        outputs=safe_outputs,
        waves_executed=result.waves_executed,
        tasks_executed=result.tasks_executed,
        events=safe_events,
    )


@router.post("/run_async", response_model=QueuedRunResponse)
async def run_pipeline_async(req: QueuedRunRequest) -> QueuedRunResponse:
    try:
        pipeline = Pipeline.model_validate(req.pipeline.model_dump())
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Invalid pipeline: {exc}")

    options = ExecutionOptions(
        per_task_timeout=req.options.per_task_timeout if req.options else None,
        fan_out_concurrency=req.options.fan_out_concurrency if req.options else None,
        backoff_jitter=req.options.backoff_jitter if req.options else 0.0,
    )

    run_id = await run_manager.submit(
        pipeline,
        inputs=req.inputs,
        session=req.session,
        options=options,
        tenant_id=req.tenant_id,
        collect_events=req.collect_events,
    )
    return QueuedRunResponse(run_id=run_id, status="queued")


@router.get("/runs/{run_id}", response_model=RunStatusResponse)
async def get_run_status(run_id: str) -> RunStatusResponse:
    rec = await run_manager.get(run_id)
    if not rec:
        raise HTTPException(status_code=404, detail="Run not found")
    return RunStatusResponse(
        run_id=rec.run_id,
        status=rec.status,
        result=_json_sanitize(rec.result) if rec.result else None,
        error=rec.error,
        events=_json_sanitize(rec.events) if rec.events else None,
    )


@router.post("/runs/{run_id}/cancel", response_model=RunStatusResponse)
async def cancel_run(run_id: str) -> RunStatusResponse:
    rec = await run_manager.get(run_id)
    if not rec:
        raise HTTPException(status_code=404, detail="Run not found")
    ok = await run_manager.cancel(run_id)
    if not ok:
        raise HTTPException(status_code=400, detail="Cannot cancel run in current state")
    rec = await run_manager.get(run_id)  # refresh
    return RunStatusResponse(
        run_id=rec.run_id,  # type: ignore[union-attr]
        status=rec.status,  # type: ignore[union-attr]
        result=_json_sanitize(rec.result) if rec and rec.result else None,
        error=rec.error if rec else None,
        events=_json_sanitize(rec.events) if rec and rec.events else None,
    )
