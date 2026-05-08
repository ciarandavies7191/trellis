"""Trellis API - FastAPI server. Requires trellis-pipelines[api]."""

try:
    from .main import app
    __all__ = ["app"]
except ImportError:
    __all__ = []
