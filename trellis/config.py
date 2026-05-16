"""Trellis runtime configuration.

Reads environment variables and provides shared helpers.  Import this module
early (e.g. in package __init__) so the proxy side-effect runs before any
HTTP calls are made.

Environment variables
---------------------
TRELLIS_HTTP_PROXY   – HTTP proxy URL, e.g. http://127.0.0.1:9000
TRELLIS_HTTPS_PROXY  – HTTPS proxy URL (defaults to TRELLIS_HTTP_PROXY if not set)
SEC_USER_AGENT       – HTTP User-Agent sent to SEC EDGAR endpoints
"""

from __future__ import annotations

import os
from typing import Any

# ---------------------------------------------------------------------------
# Proxy configuration
# ---------------------------------------------------------------------------

#: HTTP proxy URL.  Set TRELLIS_HTTP_PROXY=http://127.0.0.1:9000 when running
#: inside a corporate VPN that requires a local proxy for outbound HTTPS.
TRELLIS_HTTP_PROXY: str = os.getenv("TRELLIS_HTTP_PROXY", "")
TRELLIS_HTTPS_PROXY: str = os.getenv("TRELLIS_HTTPS_PROXY", TRELLIS_HTTP_PROXY)

# Propagate to the standard env vars used by urllib.request, httpx, and
# litellm so all HTTP stacks pick them up automatically.  We only set if not
# already present so we don't override a user's own HTTP_PROXY/HTTPS_PROXY.
if TRELLIS_HTTP_PROXY and not os.getenv("HTTP_PROXY"):
    os.environ["HTTP_PROXY"] = TRELLIS_HTTP_PROXY
if TRELLIS_HTTPS_PROXY and not os.getenv("HTTPS_PROXY"):
    os.environ["HTTPS_PROXY"] = TRELLIS_HTTPS_PROXY

# ---------------------------------------------------------------------------
# SEC configuration
# ---------------------------------------------------------------------------

SEC_USER_AGENT: str = os.getenv(
    "SEC_USER_AGENT", "Trellis/0.3 (contact@example.com)"
)

# ---------------------------------------------------------------------------
# httpx client factory
# ---------------------------------------------------------------------------

def get_http_client(timeout: int = 30, **kwargs: Any):  # type: ignore[return]
    """Return a configured ``httpx.Client`` respecting the Trellis proxy settings.

    Args:
        timeout: Request timeout in seconds.
        **kwargs: Additional kwargs forwarded to ``httpx.Client``.

    Returns:
        ``httpx.Client`` instance.  The caller is responsible for closing it
        (use as a context manager or call ``.close()``).
    """
    try:
        import httpx  # type: ignore[import]
    except ImportError as exc:
        raise ImportError(
            "httpx is required for SEC tools.  "
            "Install with: pip install trellis-pipelines[credit-dd]"
        ) from exc

    proxies: dict[str, str] = {}
    if TRELLIS_HTTPS_PROXY:
        proxies["https://"] = TRELLIS_HTTPS_PROXY
    if TRELLIS_HTTP_PROXY:
        proxies["http://"] = TRELLIS_HTTP_PROXY
    if proxies:
        kwargs.setdefault("proxies", proxies)

    kwargs.setdefault("timeout", timeout)
    return httpx.Client(**kwargs)


def get_async_http_client(timeout: int = 30, **kwargs: Any):  # type: ignore[return]
    """Return a configured ``httpx.AsyncClient`` respecting proxy settings."""
    try:
        import httpx  # type: ignore[import]
    except ImportError as exc:
        raise ImportError(
            "httpx is required for SEC tools.  "
            "Install with: pip install trellis-pipelines[credit-dd]"
        ) from exc

    proxies: dict[str, str] = {}
    if TRELLIS_HTTPS_PROXY:
        proxies["https://"] = TRELLIS_HTTPS_PROXY
    if TRELLIS_HTTP_PROXY:
        proxies["http://"] = TRELLIS_HTTP_PROXY
    if proxies:
        kwargs.setdefault("proxies", proxies)

    kwargs.setdefault("timeout", timeout)
    return httpx.AsyncClient(**kwargs)
