"""`search_web` tool — perform web search and return snippets with URLs.

Backends:
- DuckDuckGo HTML (default; no API key required)
- SerpAPI (if SERPAPI_API_KEY is set), engine=google

Inputs:
- query: str | list[str]
- top_n: int = 5
- provider: str | None ("duckduckgo" | "serpapi")
- timeout: int = 15 (seconds)

Output:
- { status: "success", results: [ { title, snippet, url, source_query } ] }
"""

from __future__ import annotations

from typing import Any, Dict, List

import json
import os
import re
import html
import urllib.parse
import urllib.request

from ..base import BaseTool, ToolInput, ToolOutput


_USER_AGENT = os.getenv("TRELLIS_USER_AGENT", "Trellis/0.1 (+https://example.com; contact@example.com)")
_DEFAULT_PROVIDER = os.getenv("TRELLIS_SEARCH_PROVIDER", "duckduckgo").strip().lower() or "duckduckgo"
_DEFAULT_TOP_N = int(os.getenv("TRELLIS_SEARCH_TOP_N", "5"))
_DEFAULT_TIMEOUT = int(os.getenv("TRELLIS_SEARCH_TIMEOUT", "15"))


def _http_get(url: str, *, timeout: int) -> bytes:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": _USER_AGENT,
            "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec - controlled URLs
        return resp.read()


def _search_serpapi(query: str, *, top_n: int, timeout: int) -> List[Dict[str, str]]:
    api_key = os.getenv("SERPAPI_API_KEY")
    if not api_key:
        return []
    params = {
        "engine": "google",
        "q": query,
        "num": str(max(1, min(top_n, 50))),
        "api_key": api_key,
    }
    url = "https://serpapi.com/search.json?" + urllib.parse.urlencode(params)
    try:
        data = _http_get(url, timeout=timeout)
        obj = json.loads(data.decode("utf-8"))
    except Exception:
        return []
    results: List[Dict[str, str]] = []
    for item in obj.get("organic_results", [])[:top_n]:
        title = item.get("title") or ""
        snippet = item.get("snippet") or item.get("snippet_highlighted_words", [""])[:1][0]
        link = item.get("link") or item.get("displayed_link") or ""
        if link:
            results.append({
                "title": str(title),
                "snippet": str(snippet),
                "url": str(link),
            })
    return results


_DDG_HTML_URL = "https://duckduckgo.com/html/"

# Regex patterns for DuckDuckGo HTML results
_A_TAG_RE = re.compile(r"<a[^>]+class=\"result__a\"[^>]+href=\"([^\"]+)\"[^>]*>(.*?)</a>", re.IGNORECASE | re.DOTALL)
_SNIPPET_RE = re.compile(r"<(a|div)[^>]+class=\"result__snippet\"[^>]*>(.*?)</(a|div)>", re.IGNORECASE | re.DOTALL)


def _clean_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _ddg_unwrap_url(href: str) -> str:
    # DuckDuckGo often wraps links like /l/?kh=-1&uddg=<urlencoded>
    try:
        if href.startswith("/"):
            # absolute path on duckduckgo.com
            parsed = urllib.parse.urlparse(href)
            qs = urllib.parse.parse_qs(parsed.query)
            utd = qs.get("uddg", [])
            if utd:
                return urllib.parse.unquote(utd[0])
            # Fallback to building absolute URL (not ideal)
            return urllib.parse.urljoin("https://duckduckgo.com", href)
    except Exception:
        pass
    return href


def _search_duckduckgo_html(query: str, *, top_n: int, timeout: int) -> List[Dict[str, str]]:
    params = {"q": query, "kl": "us-en"}
    url = _DDG_HTML_URL + "?" + urllib.parse.urlencode(params)
    try:
        raw = _http_get(url, timeout=timeout).decode("utf-8", errors="ignore")
    except Exception:
        return []

    results: List[Dict[str, str]] = []

    # Find all title anchors first
    for m in _A_TAG_RE.finditer(raw):
        href, title_html = m.group(1), m.group(2)
        url_out = _ddg_unwrap_url(html.unescape(href))
        title_out = _clean_html(title_html)

        # Attempt to find a nearby snippet: search forward from this match
        snippet_out = ""
        tail = raw[m.end(): m.end() + 2000]  # lookahead window
        sm = _SNIPPET_RE.search(tail)
        if sm:
            snippet_out = _clean_html(sm.group(2))

        results.append({
            "title": title_out,
            "snippet": snippet_out,
            "url": url_out,
        })
        if len(results) >= top_n:
            break

    return results


class SearchWebTool(BaseTool):
    def __init__(self, name: str = "search_web") -> None:
        super().__init__(name, "Perform web search and return snippets with URLs")

    def execute(
        self,
        query: str | List[str],
        *,
        top_n: int | None = None,
        provider: str | None = None,
        timeout: int | None = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        queries = query if isinstance(query, list) else [query]
        top_n = int(top_n or _DEFAULT_TOP_N)
        timeout = int(timeout or _DEFAULT_TIMEOUT)
        provider_lc = (provider or _DEFAULT_PROVIDER).strip().lower()

        all_results: List[Dict[str, Any]] = []
        for q in queries:
            q = str(q).strip()
            if not q:
                continue
            results: List[Dict[str, str]] = []
            if provider_lc == "serpapi" and os.getenv("SERPAPI_API_KEY"):
                results = _search_serpapi(q, top_n=top_n, timeout=timeout)
            if not results:
                results = _search_duckduckgo_html(q, top_n=top_n, timeout=timeout)
            for r in results:
                r["source_query"] = q
            all_results.extend(results)

        return {"status": "success", "results": all_results}

    def get_inputs(self) -> Dict[str, ToolInput]:
        return {
            "query": ToolInput(name="query", description="Query string or list of queries", required=True),
            "top_n": ToolInput(name="top_n", description="Max results per query", required=False, default=_DEFAULT_TOP_N),
            "provider": ToolInput(name="provider", description="Search provider (duckduckgo|serpapi)", required=False, default=_DEFAULT_PROVIDER),
            "timeout": ToolInput(name="timeout", description="HTTP timeout (seconds)", required=False, default=_DEFAULT_TIMEOUT),
        }

    def get_output(self) -> ToolOutput:
        return ToolOutput(name="results", description="List of search results", type_="array")

