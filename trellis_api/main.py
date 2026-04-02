from __future__ import annotations

from fastapi import FastAPI

from .routers import pipelines, plans

app = FastAPI(title="Trellis API", version="0.1.0")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(pipelines.router, prefix="/pipelines", tags=["pipelines"])  # type: ignore[attr-defined]
app.include_router(plans.router, prefix="/plans", tags=["plans"])  # type: ignore[attr-defined]
