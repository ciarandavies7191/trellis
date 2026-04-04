from __future__ import annotations

from fastapi import FastAPI
import logging

# Support both `python -m trellis_api.main` and `python trellis_api/main.py`
try:
    from trellis_api.routers import pipelines, plans
except ImportError:  # running without package context
    import os
    import sys

    sys.path.append(os.path.dirname(os.path.dirname(__file__)))
    from trellis_api.routers import pipelines, plans


def _configure_logging() -> None:
    """Configure root logging at DEBUG level if not already configured."""
    if logging.getLogger().handlers:
        return
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    logging.getLogger("trellis").setLevel(logging.DEBUG)


_configure_logging()
app = FastAPI(title="Trellis API", version="0.1.0")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(pipelines.router, prefix="/pipelines", tags=["pipelines"])  # type: ignore[attr-defined]
app.include_router(plans.router, prefix="/plans", tags=["plans"])  # type: ignore[attr-defined]


if __name__ == "__main__":
    # Run directly in IDE: python trellis_api/main.py
    import os
    import uvicorn

    host = os.environ.get("TRELLIS_API_HOST", "127.0.0.1")
    port = int(os.environ.get("TRELLIS_API_PORT", "8000"))

    # Note: reload=True requires an import string; for IDE runs we keep it False.
    uvicorn.run(app, host=host, port=port, log_level="debug")
