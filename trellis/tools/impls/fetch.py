"""Fetch tool for retrieving structured data from known sources.

Implements:
- source = "sec_edgar": fetch recent SEC EDGAR filings per company/ticker
- source = "url": generic HTTP GET (back-compat)

Notes (SEC):
- Set SEC_USER_AGENT env var to a descriptive UA per SEC policy, e.g.
  "MyApp/1.0 (me@example.com)". Requests without UA may be throttled.
- Light client-side throttling is applied between requests.
"""
from __future__ import annotations

from ..base import BaseTool, ToolInput, ToolOutput
from typing import Any, Dict, List, Iterable

import json
import os
import time
import urllib.request
import urllib.parse

# ---------------------------------------------------------------------------
# Constants / configuration
# ---------------------------------------------------------------------------

SEC_BASE = "https://data.sec.gov"
SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS_URL = SEC_BASE + "/submissions/CIK{cik}.json"

SEC_USER_AGENT = os.getenv("SEC_USER_AGENT", "Trellis/0.1 (contact@example.com)")
SEC_THROTTLE_SECONDS = float(os.getenv("SEC_THROTTLE_SECONDS", "0.2"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ticker_index_cache: dict[str, dict[str, Any]] | None = None
_name_to_cik_cache: dict[str, str] = {}
_ticker_to_cik_cache: dict[str, str] = {}


def _http_get_json(url: str) -> Any:
    req = urllib.request.Request(url, headers={
        "User-Agent": SEC_USER_AGENT,
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=20) as resp:  # nosec - controlled URL
        data = resp.read()
    try:
        return json.loads(data.decode("utf-8"))
    except Exception:
        return json.loads(data)


def _load_ticker_index() -> dict[str, dict[str, Any]]:
    global _ticker_index_cache
    if _ticker_index_cache is not None:
        return _ticker_index_cache
    # company_tickers.json is an object keyed by integer-like strings
    obj = _http_get_json(SEC_TICKERS_URL)
    index: dict[str, dict[str, Any]] = {}
    for _k, rec in obj.items():
        # rec: {ticker, title, cik_str}
        index[rec["cik_str"]] = rec
    _ticker_index_cache = index
    # Build quick-lookup caches
    _name_to_cik_cache.clear()
    _ticker_to_cik_cache.clear()
    for cik, rec in index.items():
        _name_to_cik_cache[rec["title"].lower()] = cik
        _ticker_to_cik_cache[rec["ticker"].upper()] = cik
    return index


def _pad_cik(cik: str | int) -> str:
    try:
        n = int(cik)
    except Exception:
        n = int(str(cik).strip())
    return f"{n:010d}"


def _resolve_to_ciks(entities: Iterable[str]) -> list[dict[str, str]]:
    """Resolve a list of company names or tickers to CIK strings.

    Returns list of { input, cik, ticker?, title? } preserving order; unknowns skipped.
    """
    _load_ticker_index()
    results: list[dict[str, str]] = []
    for item in entities:
        if not item:
            continue
        name = str(item).strip()
        cik: str | None = None
        meta: dict[str, Any] | None = None
        # Try ticker match (upper)
        t = name.upper()
        if t in _ticker_to_cik_cache:
            cik = _ticker_to_cik_cache[t]
            meta = _ticker_index_cache[cik] if _ticker_index_cache else None
        # Try name match (lower)
        if cik is None:
            n = name.lower()
            if n in _name_to_cik_cache:
                cik = _name_to_cik_cache[n]
                meta = _ticker_index_cache[cik] if _ticker_index_cache else None
        if cik:
            results.append({
                "input": name,
                "cik": _pad_cik(cik),
                "ticker": (meta or {}).get("ticker", ""),
                "title": (meta or {}).get("title", ""),
            })
    return results


def _fetch_sec_recent_filings(cik_padded: str) -> dict[str, Any]:
    url = SEC_SUBMISSIONS_URL.format(cik=cik_padded)
    data = _http_get_json(url)
    time.sleep(SEC_THROTTLE_SECONDS)
    return data or {}


def _build_filing_url(cik_padded: str, accession_no: str, primary_doc: str | None) -> str:
    # accession no in submissions JSON has dashes; archive path removes dashes
    acc_nodash = accession_no.replace("-", "")
    base = f"https://www.sec.gov/Archives/edgar/data/{int(cik_padded)}/{acc_nodash}"
    return f"{base}/{primary_doc}" if primary_doc else base


# ---------------------------------------------------------------------------
# Tool implementation
# ---------------------------------------------------------------------------


class FetchTool(BaseTool):
    """Tool for fetching data from external sources (SEC EDGAR, URLs)."""

    def __init__(self, name: str = "fetch_data"):
        super().__init__(name, "Retrieve structured data from external sources (sec_edgar|url)")

    def execute(
        self,
        source: str,
        *,
        url: str | None = None,
        companies: List[str] | str | None = None,
        year: int | None = None,
        forms: List[str] | None = None,
        count: int = 20,
        method: str = "GET",
        headers: Dict[str, str] | None = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        source_lc = (source or "").strip().lower()
        if source_lc in ("url", "http", "https"):
            if not url:
                raise ValueError("fetch_data(url): 'url' is required when source=url")
            req = urllib.request.Request(url, method=method.upper(), headers=headers or {})
            with urllib.request.urlopen(req, timeout=20) as resp:  # nosec - user-provided URL
                content_type = resp.headers.get("Content-Type", "")
                raw = resp.read()
            data: Any
            if content_type.startswith("application/json"):
                data = json.loads(raw.decode("utf-8", errors="replace"))
            else:
                try:
                    data = raw.decode("utf-8", errors="replace")
                except Exception:
                    data = raw
            return {
                "status": "success",
                "source": "url",
                "url": url,
                "content_type": content_type,
                "data": data,
            }

        if source_lc in ("sec", "sec_edgar", "edgar"):
            if companies is None:
                raise ValueError("fetch_data(sec_edgar): 'companies' (names or tickers) is required")
            items = companies if isinstance(companies, list) else [companies]
            resolved = _resolve_to_ciks(items)
            results: list[dict[str, Any]] = []
            wanted_forms = [f.upper() for f in (forms or [])]
            for entry in resolved:
                cik = entry["cik"]
                sub = _fetch_sec_recent_filings(cik)
                filings = []
                recent = (sub.get("filings", {}) or {}).get("recent", {})
                forms_list: list[str] = list(recent.get("form", []) or [])
                acc_list: list[str] = list(recent.get("accessionNumber", []) or [])
                date_list: list[str] = list(recent.get("filingDate", []) or [])
                doc_list: list[str] = list(recent.get("primaryDocument", []) or [])
                # Build filings
                for f, acc, dt, doc in zip(forms_list, acc_list, date_list, doc_list):
                    if wanted_forms and f.upper() not in wanted_forms:
                        continue
                    if year is not None and not (str(dt).startswith(str(year))):
                        continue
                    filings.append({
                        "form": f,
                        "filing_date": dt,
                        "accession_no": acc,
                        "url": _build_filing_url(cik, acc, doc),
                        "primary_document": doc,
                    })
                    if len(filings) >= max(1, int(count)):
                        break
                results.append({
                    "company_input": entry.get("input"),
                    "company_name": entry.get("title") or entry.get("input"),
                    "ticker": entry.get("ticker"),
                    "cik": cik,
                    "filings": filings,
                })
            return {
                "status": "success",
                "source": "sec_edgar",
                "results": results,
            }

        raise ValueError(f"Unsupported source: {source}")

    def get_inputs(self) -> Dict[str, ToolInput]:
        return {
            "source": ToolInput(name="source", description="Data source (sec_edgar|url)", required=True),
            "url": ToolInput(name="url", description="HTTP URL (when source=url)", required=False, default=None),
            "companies": ToolInput(name="companies", description="Company names or tickers (list or string)", required=False, default=None),
            "year": ToolInput(name="year", description="Filter filings by filing year (int)", required=False, default=None),
            "forms": ToolInput(name="forms", description="Filter by SEC form types (e.g., [10-K, 10-Q, 8-K])", required=False, default=None),
            "count": ToolInput(name="count", description="Max filings per company", required=False, default=20),
            "method": ToolInput(name="method", description="HTTP method for source=url", required=False, default="GET"),
            "headers": ToolInput(name="headers", description="HTTP headers for source=url", required=False, default=None),
        }

    def get_output(self) -> ToolOutput:
        return ToolOutput(
            name="data",
            description="Fetched structured data (SEC results or HTTP response)",
            type_="object",
        )
