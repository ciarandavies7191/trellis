from __future__ import annotations

from fastapi import APIRouter

from trellis_api.schemas import PlanValidateRequest, PlanValidateResponse
from trellis.models.plan import Plan

router = APIRouter()


@router.post("/validate", response_model=PlanValidateResponse)
def validate_plan(req: PlanValidateRequest) -> PlanValidateResponse:
    try:
        Plan.model_validate(req.plan.model_dump())
        return PlanValidateResponse(ok=True, message="Plan is valid")
    except Exception as exc:  # noqa: BLE001
        return PlanValidateResponse(ok=False, errors=[str(exc)], message="Validation failed")

