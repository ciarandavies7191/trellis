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
import logging
import os
import threading
import time
import urllib.request
import urllib.parse

from ..decorators import export_io

logger = logging.getLogger(__name__)

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
_index_lock = threading.Lock()


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
    # Fast path — already loaded (no lock needed for read after write).
    if _ticker_index_cache is not None:
        return _ticker_index_cache
    # Slow path — download once, under a lock so parallel threads don't race.
    with _index_lock:
        if _ticker_index_cache is not None:  # re-check after acquiring
            return _ticker_index_cache
        logger.debug("fetch_data: loading SEC ticker index from %s", SEC_TICKERS_URL)
        obj = _http_get_json(SEC_TICKERS_URL)
        index: dict[str, dict[str, Any]] = {}
        new_name_cache: dict[str, str] = {}
        new_ticker_cache: dict[str, str] = {}
        for _k, rec in obj.items():
            cik_key = rec["cik_str"]
            index[cik_key] = rec
            new_name_cache[rec["title"].lower()] = cik_key
            new_ticker_cache[rec["ticker"].upper()] = cik_key
        # Assign atomically — other threads only see complete caches.
        _name_to_cik_cache.update(new_name_cache)
        _ticker_to_cik_cache.update(new_ticker_cache)
        _ticker_index_cache = index
        logger.debug("fetch_data: ticker index loaded (%d companies)", len(index))
    return _ticker_index_cache


def _pad_cik(cik: str | int) -> str:
    try:
        n = int(cik)
    except Exception:
        n = int(str(cik).strip())
    return f"{n:010d}"


def resolve_ticker_to_cik(ticker: str) -> str:
    """Public API: resolve a ticker symbol to a zero-padded 10-digit CIK string.

    Args:
        ticker: Exchange ticker symbol (case-insensitive).

    Returns:
        Zero-padded 10-digit CIK string, e.g. ``"0000009092"``.

    Raises:
        ValueError: if the ticker is not found in the SEC EDGAR index.
    """
    _load_ticker_index()
    t = ticker.strip().upper()
    cik = _ticker_to_cik_cache.get(t)
    if cik is None:
        raise ValueError(
            f"Ticker {ticker!r} not found in SEC EDGAR ticker index. "
            "Use the current exchange ticker symbol (e.g. 'META', 'CVS')."
        )
    return _pad_cik(cik)


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
                meta = _ticker_index_cache.get(cik) if _ticker_index_cache else None

        # Fuzzy ticker fallback: SEC sometimes lists GOOG but not GOOGL (share-class
        # suffix). Try progressively shorter prefixes (e.g. GOOGL → GOOG → GOO).
        if cik is None and len(t) > 2:
            for trim in range(1, min(4, len(t) - 1)):
                prefix = t[:-trim]
                if prefix in _ticker_to_cik_cache:
                    cik = _ticker_to_cik_cache[prefix]
                    meta = _ticker_index_cache.get(cik) if _ticker_index_cache else None
                    logger.debug(
                        "fetch_data: ticker %r not found; matched via prefix %r", t, prefix
                    )
                    break

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

@export_io(path="debug/tools")
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
        ticker: List[str] | str | None = None,
        year: int | None = None,
        period_end: str | None = None,
        period_type: str | None = None,
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
            # Accept `ticker` as an alias for `companies` (spreading pipelines use ticker=)
            if companies is None and ticker is not None:
                companies = ticker

            if companies is None:
                raise ValueError("fetch_data(sec_edgar): 'companies' (names or tickers) is required")

            # Derive year from period_end when year is not supplied (e.g. "2025-03-31" → 2025)
            if year is None and period_end:
                try:
                    year = int(str(period_end).split("-")[0])
                except (ValueError, IndexError):
                    pass

            # Derive form filter from period_type when forms is not supplied.
            # annual → 10-K only; ytd_current / ytd_prior / quarterly → 10-Q only.
            if forms is None and period_type:
                pt = str(period_type).lower()
                if pt == "annual":
                    forms = ["10-K"]
                elif pt in ("ytd_current", "ytd_prior", "quarterly", "q"):
                    forms = ["10-Q"]

            # Annual forms (10-K) are filed in the calendar year AFTER the fiscal
            # year end (e.g. FY 2025 → 10-K filed Feb 2026). Accept year+1 as well
            # so period_end-derived year filters don't miss these filings.
            _ANNUAL_FORMS = frozenset({"10-K", "10-KT", "20-F", "40-F"})
            wanted_forms = [f.upper() for f in (forms or [])]
            is_annual_filter = bool(wanted_forms) and all(f in _ANNUAL_FORMS for f in wanted_forms)
            allowed_years: set[str] | None = None
            if year is not None:
                allowed_years = {str(year)}
                if is_annual_filter:
                    allowed_years.add(str(year + 1))

            items = companies if isinstance(companies, list) else [companies]
            resolved = _resolve_to_ciks(items)
            resolved_inputs = {r["input"] for r in resolved}
            unresolved = [i for i in items if i and i.strip() and i.strip() not in resolved_inputs]
            if unresolved:
                raise ValueError(
                    f"fetch_data(sec_edgar): could not resolve the following companies to a CIK "
                    f"in the SEC EDGAR ticker index: {unresolved}. "
                    f"Use the current ticker symbol (e.g. 'META') or the exact SEC-registered "
                    f"company name (e.g. 'Meta Platforms, Inc.'). "
                    f"Companies that have rebranded must be referenced by their current name."
                )
            results: list[dict[str, Any]] = []
            for entry in resolved:
                cik = entry["cik"]
                sub = _fetch_sec_recent_filings(cik)
                recent = (sub.get("filings", {}) or {}).get("recent", {})
                forms_list: list[str] = list(recent.get("form", []) or [])
                acc_list: list[str] = list(recent.get("accessionNumber", []) or [])
                date_list: list[str] = list(recent.get("filingDate", []) or [])
                doc_list: list[str] = list(recent.get("primaryDocument", []) or [])
                report_date_list: list[str] = list(recent.get("reportDate", []) or [])
                # Pad reportDate list if shorter (older EDGAR records may omit it)
                while len(report_date_list) < len(forms_list):
                    report_date_list.append("")

                # Collect all candidate filings that pass form/year filters.
                # We do NOT apply count here so we can do period-end matching first.
                candidates: list[dict[str, Any]] = []
                for f, acc, dt, doc, rd in zip(
                    forms_list, acc_list, date_list, doc_list, report_date_list
                ):
                    if wanted_forms and f.upper() not in wanted_forms:
                        continue
                    if allowed_years is not None and str(dt)[:4] not in allowed_years:
                        continue
                    candidates.append({
                        "form": f,
                        "filing_date": dt,
                        "report_date": rd,
                        "accession_no": acc,
                        "url": _build_filing_url(cik, acc, doc),
                        "primary_document": doc,
                    })

                # When period_end is specified, prefer the filing whose reportDate
                # matches it exactly, then accept the closest earlier date.
                # This prevents count=1 from grabbing the latest filing in the year
                # (e.g., Q3 2025) when Q1 2025 is the target.
                filings: list[dict[str, Any]] = []
                if period_end and candidates:
                    exact = [c for c in candidates if c["report_date"] == period_end]
                    if exact:
                        filings = exact[:max(1, int(count))]
                    else:
                        # Take closest filing whose reportDate is ≤ period_end
                        before = [c for c in candidates if c["report_date"] <= period_end and c["report_date"]]
                        if before:
                            # Sort ascending by report_date; take the closest (last)
                            before_sorted = sorted(before, key=lambda x: x["report_date"])
                            filings = before_sorted[-max(1, int(count)):]
                        else:
                            filings = candidates[:max(1, int(count))]
                else:
                    filings = candidates[:max(1, int(count))]

                # Drop the internal report_date field from outgoing filing dicts
                clean_filings = [
                    {k: v for k, v in c.items() if k != "report_date"}
                    for c in filings
                ]
                results.append({
                    "company_input": entry.get("input"),
                    "company_name": entry.get("title") or entry.get("input"),
                    "ticker": entry.get("ticker"),
                    "cik": cik,
                    "filings": clean_filings,
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
            "ticker": ToolInput(name="ticker", description="Alias for companies — single ticker string or list", required=False, default=None),
            "year": ToolInput(name="year", description="Filter filings by filing year (int)", required=False, default=None),
            "period_end": ToolInput(name="period_end", description="ISO period-end date (YYYY-MM-DD); year extracted for filing filter when year is not set", required=False, default=None),
            "period_type": ToolInput(name="period_type", description="annual|ytd_current|ytd_prior — infers form filter (10-K / 10-Q) when forms is not set", required=False, default=None),
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
