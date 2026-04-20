"""Canonical finance function implementations.

These are the built-in registered functions for the finance domain. Each
function is deterministic and does not require an LLM invocation. They are
registered in a shared default FunctionRegistry at application startup.

Functions
---------
fiscal_period_logic       — Resolve fiscal periods for a given date and company
ticker_lookup             — Resolve a company name to its primary exchange ticker
financial_scale_normalize — Convert a financial value between currency/scale
period_label              — Produce a standardised period label string
fiscal_year_end           — Return the fiscal year-end month/day for a company
calculate_derived_fields  — Compute derived financial metrics from raw extracted values
apply_segment_names       — Replace placeholder segment keys with actual segment names
"""

from __future__ import annotations

import datetime
import json
import re
from typing import Any

from trellis.models.handles import PeriodDescriptor
from trellis.registry.functions import FunctionRegistry, RegisteredFunction

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Mapping of company identifiers (lowercased) to their fiscal year-end as
# (month, day) tuples. Real deployments should replace this with a database
# or external API call.
_FISCAL_YEAR_ENDS: dict[str, tuple[int, int]] = {
    # Standard calendar year-end
    "default": (12, 31),
    # Well-known non-calendar fiscal years
    "apple": (9, 30),
    "microsoft": (6, 30),
    "walmart": (1, 31),
    "amazon": (12, 31),
    "alphabet": (12, 31),
    "google": (12, 31),
    "meta": (12, 31),
    "tesla": (12, 31),
    "berkshire": (12, 31),
}

# Simplified ticker lookup: maps lowercased company names to primary tickers.
_TICKER_MAP: dict[str, str] = {
    "apple": "AAPL",
    "microsoft": "MSFT",
    "amazon": "AMZN",
    "alphabet": "GOOGL",
    "google": "GOOGL",
    "meta": "META",
    "tesla": "TSLA",
    "berkshire": "BRK.B",
    "walmart": "WMT",
    "jpmorgan": "JPM",
    "jp morgan": "JPM",
    "bank of america": "BAC",
    "citigroup": "C",
    "goldman sachs": "GS",
    "morgan stanley": "MS",
    "exxon": "XOM",
    "chevron": "CVX",
    "johnson & johnson": "JNJ",
    "johnson and johnson": "JNJ",
    "pfizer": "PFE",
    "unitedhealth": "UNH",
}

# Currency exchange rates relative to USD (stub values for deterministic tests).
_FX_RATES_TO_USD: dict[str, float] = {
    "USD": 1.0,
    "EUR": 1.08,
    "GBP": 1.27,
    "JPY": 0.0067,
    "CAD": 0.74,
    "AUD": 0.65,
    "CHF": 1.13,
    "CNY": 0.14,
    "HKD": 0.13,
}

# Scale multipliers (all relative to "ones")
_SCALE_MULTIPLIERS: dict[str, float] = {
    "ones": 1.0,
    "hundreds": 1e2,
    "thousands": 1e3,
    "millions": 1e6,
    "billions": 1e9,
    "trillions": 1e12,
}


def _parse_date(date_str: str) -> datetime.date:
    """Parse an ISO-format date string (YYYY-MM-DD)."""
    return datetime.date.fromisoformat(date_str)


def _fiscal_year_end_for(company: str) -> tuple[int, int]:
    key = company.lower().strip()
    # Try longest prefix match
    for name in sorted(_FISCAL_YEAR_ENDS, key=len, reverse=True):
        if name in key:
            return _FISCAL_YEAR_ENDS[name]
    return _FISCAL_YEAR_ENDS["default"]


# ---------------------------------------------------------------------------
# Function implementations
# ---------------------------------------------------------------------------


def fiscal_period_logic(as_of_date: str, company: str) -> list[PeriodDescriptor]:
    """
    Return 1 or 3 PeriodDescriptors depending on whether *as_of_date* is a
    fiscal year-end for *company*.

    - If the date matches the fiscal year-end, returns a single annual descriptor.
    - Otherwise, returns three descriptors: the most recent completed fiscal year,
      the current YTD period, and the prior-year YTD comparison period.
    """
    date = _parse_date(as_of_date)
    fye_month, fye_day = _fiscal_year_end_for(company)

    # Determine whether as_of_date falls on or after the most recent FYE
    try:
        this_year_fye = datetime.date(date.year, fye_month, fye_day)
    except ValueError:
        # Handle e.g. Feb 29 in non-leap years — use Feb 28
        this_year_fye = datetime.date(date.year, fye_month, 28)

    # Check whether we are at the fiscal year-end
    is_year_end = date == this_year_fye

    if is_year_end:
        fy_label = f"FY {date.year}" if fye_month == 12 else f"FY {date.year - 1}/{date.year}"
        return [
            PeriodDescriptor(
                label=fy_label,
                period_end=date.isoformat(),
                period_type="annual",
                is_annual=True,
            )
        ]

    # Find the most recent completed FYE
    if this_year_fye <= date:
        last_fye = this_year_fye
        prev_fye_year = date.year - 1
    else:
        last_fye = datetime.date(date.year - 1, fye_month, fye_day)
        prev_fye_year = date.year - 2

    try:
        prev_fye = datetime.date(prev_fye_year, fye_month, fye_day)
    except ValueError:
        prev_fye = datetime.date(prev_fye_year, fye_month, 28)

    # Quarter label helper
    quarter = (date.month - 1) // 3 + 1
    ytd_label = f"Q{quarter} {date.year}"

    # Prior-year comparable period (same month/day, year minus 1)
    try:
        prior_period_end = datetime.date(date.year - 1, date.month, date.day)
    except ValueError:
        prior_period_end = datetime.date(date.year - 1, date.month, 28)

    prior_quarter = (prior_period_end.month - 1) // 3 + 1
    prior_label = f"Q{prior_quarter} {prior_period_end.year}"

    annual_label = f"FY {last_fye.year}" if fye_month == 12 else f"FY {last_fye.year - 1}/{last_fye.year}"

    return [
        PeriodDescriptor(
            label=annual_label,
            period_end=last_fye.isoformat(),
            period_type="annual",
            is_annual=True,
        ),
        PeriodDescriptor(
            label=ytd_label,
            period_end=date.isoformat(),
            period_type="ytd_current",
            is_annual=False,
        ),
        PeriodDescriptor(
            label=prior_label,
            period_end=prior_period_end.isoformat(),
            period_type="ytd_prior",
            is_annual=False,
        ),
    ]


def ticker_lookup(company: str) -> str:
    """
    Resolve a company name to its primary exchange ticker.

    Raises:
        ValueError: if no ticker mapping exists for *company*.
    """
    key = company.lower().strip()
    # Exact match first
    if key in _TICKER_MAP:
        return _TICKER_MAP[key]
    # Substring match (longest first for disambiguation)
    for name in sorted(_TICKER_MAP, key=len, reverse=True):
        if name in key:
            return _TICKER_MAP[name]
    raise ValueError(
        f"No ticker mapping found for company {company!r}. "
        "Register a mapping in trellis.registry.finance_functions._TICKER_MAP."
    )


def financial_scale_normalize(
    value: Any,
    source_currency: str,
    target_currency: str,
    target_scale: str,
) -> float:
    """
    Convert a financial value from *source_currency* to *target_currency*
    and express it in *target_scale* units (e.g. "millions").

    Args:
        value:           Numeric value (will be coerced to float).
        source_currency: ISO 4217 currency code of the input value.
        target_currency: ISO 4217 currency code for the output value.
        target_scale:    Output scale: "ones", "thousands", "millions",
                         "billions", or "trillions".

    Returns:
        Converted and scaled float.

    Raises:
        ValueError: if source or target currency or scale is unknown.
    """
    numeric = float(value)

    src = source_currency.upper()
    tgt = target_currency.upper()
    scale = target_scale.lower()

    if src not in _FX_RATES_TO_USD:
        raise ValueError(f"Unknown source currency {src!r}.")
    if tgt not in _FX_RATES_TO_USD:
        raise ValueError(f"Unknown target currency {tgt!r}.")
    if scale not in _SCALE_MULTIPLIERS:
        raise ValueError(
            f"Unknown target scale {scale!r}. "
            f"Valid: {sorted(_SCALE_MULTIPLIERS)}"
        )

    # Convert to USD then to target currency
    usd_value = numeric * _FX_RATES_TO_USD[src]
    target_value = usd_value / _FX_RATES_TO_USD[tgt]

    # Apply scale
    return target_value / _SCALE_MULTIPLIERS[scale]


def period_label(date: str, period_type: str) -> str:
    """
    Produce a standardized period label for a given date and period type.

    Args:
        date:        ISO date string (YYYY-MM-DD).
        period_type: One of "annual", "quarterly", "ytd_current", "ytd_prior",
                     "half_year".

    Returns:
        Human-readable label, e.g. "Q1 2025" or "FY 2024".

    Raises:
        ValueError: if *period_type* is not recognized.
    """
    d = _parse_date(date)
    ptype = period_type.lower()

    if ptype in ("annual", "fy"):
        return f"FY {d.year}"
    elif ptype in ("quarterly", "ytd_current", "ytd_prior", "q"):
        quarter = (d.month - 1) // 3 + 1
        return f"Q{quarter} {d.year}"
    elif ptype in ("half_year", "h1", "h2"):
        half = 1 if d.month <= 6 else 2
        return f"H{half} {d.year}"
    else:
        raise ValueError(
            f"Unknown period_type {period_type!r}. "
            "Valid: annual, quarterly, ytd_current, ytd_prior, half_year."
        )


def fiscal_year_end(company: str) -> str:
    """
    Return the fiscal year-end as a month/day string (MM-DD) for *company*.

    Examples:
        "apple"     → "09-30"
        "microsoft" → "06-30"
        "amazon"    → "12-31"
    """
    month, day = _fiscal_year_end_for(company)
    return f"{month:02d}-{day:02d}"


# ---------------------------------------------------------------------------
# Derived-field helpers
# ---------------------------------------------------------------------------

_FIELD_NOT_FOUND = "__not_found__"


def _to_float(value: Any) -> float | None:
    """Coerce *value* to float, returning None on failure or sentinel."""
    if value is None or value == _FIELD_NOT_FOUND:
        return None
    try:
        # Strip commas and whitespace common in financial strings
        return float(str(value).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def _pct(numerator: float | None, denominator: float | None) -> str:
    """Return percentage string rounded to 2 dp, or sentinel if inputs invalid."""
    if numerator is None or denominator is None or denominator == 0:
        return _FIELD_NOT_FOUND
    return str(round(numerator / denominator * 100, 2))


def _coerce_list_of_dicts(value: Any) -> list[dict[str, Any]]:
    """Coerce *value* to a list of dicts. Handles JSON strings from llm_job.

    Robust to LLM responses that append prose after the closing code fence,
    e.g. ```json\\n[...]\\n```\\n\\n**Notes:** ...
    Uses a greedy regex scan to locate the JSON array anywhere in the string.
    """
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        # Fast path: try the whole string first (common case when output is clean)
        try:
            parsed = json.loads(value.strip())
            if isinstance(parsed, list):
                return parsed
        except Exception:
            pass
        # Scan for the first [...] span — greedy, handles fences + trailing prose
        m = re.search(r"\[.*\]", value, flags=re.S)
        if m:
            try:
                parsed = json.loads(m.group(0))
                if isinstance(parsed, list):
                    return parsed
            except Exception:
                pass
    return []


def calculate_derived_fields(extracted_fields: Any) -> list[dict[str, Any]]:
    """
    Compute derived financial metrics for each period dict in *extracted_fields*.

    Metrics added (all as string representations):
        gross_profit          = revenue - cost_of_revenue
        gross_margin_pct      = gross_profit / revenue × 100
        operating_margin_pct  = operating_income / revenue × 100
        net_margin_pct        = net_income / revenue × 100
        effective_tax_rate    = income_tax_expense / pretax_income × 100

    The canonical field names used as inputs follow the income-statement
    spreading template conventions. Any field that cannot be computed because
    its inputs are absent or non-numeric is set to ``"__not_found__"``.

    Args:
        extracted_fields: List of dicts (or a JSON string) one per period, as
                          produced by the ``extract_fields`` tool or after
                          reconciliation.

    Returns:
        A new list of dicts with the derived metrics added to each period dict.
    """
    extracted_fields = _coerce_list_of_dicts(extracted_fields)
    result: list[dict[str, Any]] = []
    for period in extracted_fields:
        out = dict(period)

        revenue = _to_float(period.get("revenue") or period.get("total_revenue") or period.get("revenues"))
        cost_of_revenue = _to_float(
            period.get("cost_of_revenue")
            or period.get("cost_of_revenues")
            or period.get("cost_of_goods_sold")
        )
        operating_income = _to_float(
            period.get("operating_income")
            or period.get("income_from_operations")
        )
        net_income = _to_float(
            period.get("net_income")
            or period.get("net_earnings")
        )
        pretax_income = _to_float(
            period.get("pretax_income")
            or period.get("income_before_taxes")
            or period.get("income_before_income_taxes")
        )
        tax_expense = _to_float(
            period.get("income_tax_expense")
            or period.get("provision_for_income_taxes")
        )

        # Gross Profit
        if revenue is not None and cost_of_revenue is not None:
            gp = revenue - cost_of_revenue
            out["gross_profit"] = str(int(round(gp)))
        else:
            out["gross_profit"] = _FIELD_NOT_FOUND

        gp_val = _to_float(out.get("gross_profit"))
        out["gross_margin_pct"] = _pct(gp_val, revenue)
        out["operating_margin_pct"] = _pct(operating_income, revenue)
        out["net_margin_pct"] = _pct(net_income, revenue)
        out["effective_tax_rate"] = _pct(tax_expense, pretax_income)

        result.append(out)
    return result


def apply_segment_names(
    extracted_fields: Any,
    segment_names: Any,
) -> list[dict[str, Any]]:
    """
    Replace placeholder segment-index keys with resolved segment names.

    When the spreading template uses generic numbered placeholders such as
    ``revenue_segment_[1]``, ``revenue_segment_[2]``, etc., this function
    renames those keys to the actual segment names discovered by the
    ``generate_analyst_notes`` LLM step.

    Args:
        extracted_fields: List of per-period dicts from the extraction pipeline.
        segment_names:    Mapping from placeholder index (e.g. ``"[1]"``,
                          ``"1"``, ``"segment_1"``) to resolved name
                          (e.g. ``"Google Services"``).

    Returns:
        A new list of dicts with placeholder keys renamed.
    """
    extracted_fields = _coerce_list_of_dicts(extracted_fields)

    # Coerce segment_names JSON string if needed
    if isinstance(segment_names, str):
        stripped = re.sub(r"^```[a-z]*\s*", "", segment_names.strip(), flags=re.I)
        stripped = re.sub(r"\s*```$", "", stripped)
        try:
            parsed = json.loads(stripped)
            if isinstance(parsed, dict):
                segment_names = parsed
        except Exception:
            segment_names = {}

    if not segment_names or not isinstance(segment_names, dict):
        return extracted_fields

    # Normalise keys: accept "[1]", "1", "segment_1" → always match "[N]" suffix
    normalised: dict[str, str] = {}
    for k, v in segment_names.items():
        k_str = str(k).strip()
        # Extract bare digit(s)
        m = re.search(r"\d+", k_str)
        if m:
            normalised[f"[{m.group(0)}]"] = str(v).strip()

    result: list[dict[str, Any]] = []
    for period in extracted_fields:
        out: dict[str, Any] = {}
        for field_key, field_val in period.items():
            new_key = field_key
            for placeholder, name in normalised.items():
                if placeholder in field_key:
                    # Replace the placeholder with a slug of the segment name
                    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
                    new_key = field_key.replace(placeholder, f"_{slug}")
                    break
            out[new_key] = field_val
        result.append(out)
    return result


# ---------------------------------------------------------------------------
# Section assembly and cross-check validation
# ---------------------------------------------------------------------------


def _get(period: dict, *keys: str) -> float | None:
    """Try multiple field name variants; return first non-sentinel float found."""
    for k in keys:
        v = _to_float(period.get(k))
        if v is not None:
            return v
    return None


def assemble_extraction_sections(
    face: Any,
    segments: Any,
    other_income: Any,
    per_share: Any,
) -> list[dict[str, Any]]:
    """
    Merge four section-specific extraction outputs into unified per-period dicts.

    Each input is a list of period dicts (or a JSON string thereof) containing
    the fields for one section. Lists are merged positionally: face[i] + segments[i]
    + other_income[i] + per_share[i] → one dict per period.

    Args:
        face:         List of dicts with income statement face fields.
        segments:     List of dicts with segment revenue and OI fields.
        other_income: List of dicts with other income decomposition fields.
        per_share:    List of dicts with EPS and share count fields.

    Returns:
        Unified list of period dicts, one per period.
    """
    face_list = _coerce_list_of_dicts(face)
    seg_list = _coerce_list_of_dicts(segments)
    oi_list = _coerce_list_of_dicts(other_income)
    eps_list = _coerce_list_of_dicts(per_share)

    n = max(len(face_list), len(seg_list), len(oi_list), len(eps_list))
    result: list[dict[str, Any]] = []
    for i in range(n):
        merged: dict[str, Any] = {}
        for lst in (face_list, seg_list, oi_list, eps_list):
            if i < len(lst):
                # Section values win over _FIELD_NOT_FOUND from earlier sections
                for k, v in lst[i].items():
                    if k not in merged or merged[k] == _FIELD_NOT_FOUND:
                        merged[k] = v
        result.append(merged)
    return result


def compute_derived_fields(
    extracted_fields: Any,
    periods: Any = None,
) -> list[dict[str, Any]]:
    """
    Deterministically compute all derived financial metrics for each period dict.

    Fields computed (using the exact template field names as output keys):
        Gross Profit              = Total Revenues − Cost of Revenues
        Gross Margin (%)          = Gross Profit ÷ Total Revenues × 100
        Operating Margin (%)      = Operating Income ÷ Total Revenues × 100
        Net Margin (%)            = Net Income ÷ Total Revenues × 100
        Effective Tax Rate (%)    = Provision for Income Taxes ÷ Income Before Income Taxes × 100
        Total Operating Expenses  = CoR + R&D + S&M + G&A + Restructuring
        YoY Growth (%)            = (current − prior) ÷ prior × 100 (requires periods arg)

    Also runs a units plausibility check: if Total Revenues < 100 for a company
    that should be reporting in millions, logs a warning about possible unit error.

    Args:
        extracted_fields: List of dicts (or JSON string) one per period.
        periods:          Optional list of PeriodDescriptor dicts/objects. When
                          provided, used to identify ytd_current vs ytd_prior for
                          YoY Growth calculation.

    Returns:
        New list of dicts with derived fields added.
    """
    import logging as _logging
    _log = _logging.getLogger(__name__)

    extracted_fields = _coerce_list_of_dicts(extracted_fields)
    # Coerce periods if provided
    if periods is not None and not isinstance(periods, list):
        periods_list = _coerce_list_of_dicts(periods)
    else:
        periods_list = periods or []

    result: list[dict[str, Any]] = []
    for i, period in enumerate(extracted_fields):
        out = dict(period)

        revenue = _get(period, "Total Revenues")
        cor = _get(period, "Cost of Revenues")
        rd = _get(period, "Research and Development")
        sm = _get(period, "Sales and Marketing")
        ga = _get(period, "General and Administrative")
        restr = _get(period, "Restructuring and Other Charges")
        oi = _get(period, "Operating Income (Loss)")
        net = _get(period, "Net Income (Loss)")
        pretax = _get(period, "Income Before Income Taxes")
        tax = _get(period, "Provision for Income Taxes")

        # Plausibility check: large-cap companies should have revenues > $100M
        if revenue is not None and 0 < revenue < 100:
            _log.warning(
                "compute_derived: period %d Total Revenues = %s — suspiciously small. "
                "Possible units error (thousands reported, millions expected).",
                i, revenue,
            )

        # Total Operating Expenses
        opex_components = [c for c in [cor, rd, sm, ga, restr or 0.0] if c is not None]
        if len(opex_components) >= 2:  # need at least CoR + one opex line
            out["Total Operating Expenses"] = str(int(round(sum(opex_components))))
        else:
            out["Total Operating Expenses"] = _FIELD_NOT_FOUND

        # Gross Profit
        if revenue is not None and cor is not None:
            gp = revenue - cor
            out["Gross Profit"] = str(int(round(gp)))
        else:
            out["Gross Profit"] = _FIELD_NOT_FOUND

        gp_val = _to_float(out.get("Gross Profit"))
        out["Gross Margin (%)"] = _pct(gp_val, revenue)
        out["Operating Margin (%)"] = _pct(oi, revenue)
        out["Net Margin (%)"] = _pct(net, revenue)
        out["Effective Tax Rate (%)"] = _pct(tax, pretax)

        result.append(out)

    # YoY Growth: pair ytd_current with ytd_prior, or use index order when no metadata
    if len(result) >= 2:
        # Try to identify periods by type from periods_list
        current_idx: int | None = None
        prior_idx: int | None = None
        for j, p in enumerate(periods_list):
            ptype = (p.get("period_type") if isinstance(p, dict) else getattr(p, "period_type", None)) or ""
            if ptype == "ytd_current":
                current_idx = j
            elif ptype == "ytd_prior":
                prior_idx = j

        if current_idx is not None and prior_idx is not None and current_idx < len(result) and prior_idx < len(result):
            curr_rev = _to_float(result[current_idx].get("Total Revenues"))
            prior_rev = _to_float(result[prior_idx].get("Total Revenues"))
            result[current_idx]["YoY Growth (%)"] = _pct(
                (curr_rev - prior_rev) if curr_rev is not None and prior_rev is not None else None,
                prior_rev,
            )

    return result


def validate_cross_checks(
    extracted_fields: Any,
    periods: Any = None,
) -> list[dict[str, Any]]:
    """
    Run the arithmetic cross-checks defined in the spreading manual v2.

    Checks (per period, per manual v2 cross-check summary §§X1–X10):
        1. (X1) Segment Revenue reconciliation:
           Seg Rev [1] + [2] + [3] + Other/Eliminations == Total Revenues ± 1
        2. (X3) Total Operating Expenses reconciliation:
           Total Revenues − Total Operating Expenses == Operating Income ± 1
        3. (X4) Segment OI reconciliation:
           Seg OI [1] + [2] + [3] + Unallocated == Operating Income ± 1
        4. (X5) OI&E component reconciliation:
           Interest Income − Interest Expense + FX Gains + Investment Gains
           + Other Non-Op == Total Other Income ± 1
           Note: Interest Expense is stored as a POSITIVE number (§5.2) and
           is SUBTRACTED in this formula.
        5. (X10) EPS plausibility:
           Net Income ÷ Wtd Avg Basic Shares ≈ Basic EPS ± 0.02

    Args:
        extracted_fields: List of period dicts (post compute_derived_fields).
        periods:          Optional list of PeriodDescriptor dicts/objects used
                          for labeling failures.

    Returns:
        List of failure dicts. Empty list means all checks passed.
        Each failure dict contains: check_id, period_label, period_index,
        description, section, computed_value, reported_value, discrepancy.
    """
    extracted_fields = _coerce_list_of_dicts(extracted_fields)
    failures: list[dict[str, Any]] = []

    for i, period in enumerate(extracted_fields):
        # Resolve period label for error messages
        label = _FIELD_NOT_FOUND
        if periods and i < len(periods):
            p = periods[i]
            label = (p.get("label") if isinstance(p, dict) else getattr(p, "label", str(i))) or str(i)

        # --- Check 1: Segment Revenue reconciliation ---
        seg1 = _get(period, "Segment Revenue — [1]")
        seg2 = _get(period, "Segment Revenue — [2]")
        seg3 = _get(period, "Segment Revenue — [3]")
        elim = _get(period, "Other / Eliminations") or 0.0
        total_rev = _get(period, "Total Revenues")

        if seg1 is not None and seg2 is not None and total_rev is not None:
            seg_sum = (seg1 or 0) + (seg2 or 0) + (seg3 or 0) + elim
            discrepancy = abs(seg_sum - total_rev)
            if discrepancy > 1:
                failures.append({
                    "check_id": "segment_revenue_reconciliation",
                    "period_label": label,
                    "period_index": i,
                    "description": (
                        f"Segment revenues do not sum to Total Revenues. "
                        f"Segment sum={seg_sum:.0f}, Total Revenues={total_rev:.0f}, "
                        f"discrepancy={discrepancy:.0f}. "
                        "Segment Revenue rows may contain OI values instead of revenue values."
                    ),
                    "section": "segments",
                    "computed_value": round(seg_sum, 2),
                    "reported_value": round(total_rev, 2),
                    "discrepancy": round(discrepancy, 2),
                })

        # --- Check 2: Revenue − Total OpEx == Operating Income ---
        total_opex = _get(period, "Total Operating Expenses")
        oi = _get(period, "Operating Income (Loss)")

        if total_rev is not None and total_opex is not None and oi is not None:
            implied_oi = total_rev - total_opex
            discrepancy = abs(implied_oi - oi)
            if discrepancy > 1:
                failures.append({
                    "check_id": "opex_oi_reconciliation",
                    "period_label": label,
                    "period_index": i,
                    "description": (
                        f"Total Revenues − Total Operating Expenses ≠ Operating Income. "
                        f"Implied OI={implied_oi:.0f}, Reported OI={oi:.0f}, "
                        f"discrepancy={discrepancy:.0f}. "
                        "Total OpEx definition may be incorrect (should include Cost of Revenues)."
                    ),
                    "section": "face",
                    "computed_value": round(implied_oi, 2),
                    "reported_value": round(oi, 2),
                    "discrepancy": round(discrepancy, 2),
                })

        # --- Check 3: Segment OI reconciliation ---
        soi1 = _get(period, "Segment OI — [1]")
        soi2 = _get(period, "Segment OI — [2]")
        soi3 = _get(period, "Segment OI — [3]")
        unalloc = _get(period, "Unallocated / Corporate") or 0.0

        if soi1 is not None and soi2 is not None and oi is not None:
            soi_sum = (soi1 or 0) + (soi2 or 0) + (soi3 or 0) + unalloc
            discrepancy = abs(soi_sum - oi)
            if discrepancy > 1:
                failures.append({
                    "check_id": "segment_oi_reconciliation",
                    "period_label": label,
                    "period_index": i,
                    "description": (
                        f"Segment OI lines do not sum to Operating Income. "
                        f"Segment OI sum={soi_sum:.0f}, Reported OI={oi:.0f}, "
                        f"discrepancy={discrepancy:.0f}. "
                        "Check Unallocated/Corporate line and individual segment OI values."
                    ),
                    "section": "segments",
                    "computed_value": round(soi_sum, 2),
                    "reported_value": round(oi, 2),
                    "discrepancy": round(discrepancy, 2),
                })

        # --- Check 4 (X5): OI&E component reconciliation ---
        # Formula: Int Income − Int Expense + FX + Inv Gains + Other == Total OI&E
        # Interest Expense is stored as a POSITIVE number per manual v2 §5.2 and
        # is SUBTRACTED here. Cross-check tolerance ±1 (in filing units, e.g. $M).
        int_income = _get(period, "Interest Income")
        int_expense = _get(period, "Interest Expense")
        fx_gains = _get(period, "FX Gains (Losses), Net")
        inv_gains = _get(period, "Gains (Losses) on Investments, Net")
        other_nonop = _get(period, "Other Non-Operating, Net")
        total_oie = _get(period, "Total Other Income (Expense), Net")

        if int_income is not None and int_expense is not None and total_oie is not None:
            oie_sum = (
                int_income
                - int_expense  # IE is positive, so subtract
                + (fx_gains or 0.0)
                + (inv_gains or 0.0)
                + (other_nonop or 0.0)
            )
            discrepancy = abs(oie_sum - total_oie)
            if discrepancy > 1:
                failures.append({
                    "check_id": "oie_component_reconciliation",
                    "period_label": label,
                    "period_index": i,
                    "description": (
                        f"OI&E components do not reconcile to Total Other Income. "
                        f"Component sum (Int Inc − Int Exp + FX + Inv Gains + Other) = {oie_sum:.0f}, "
                        f"Reported Total OI&E = {total_oie:.0f}, "
                        f"discrepancy = {discrepancy:.0f}. "
                        "Possible causes: missing investment gains alias (check 'Net gain on equity "
                        "securities'), wrong Interest Expense sign (should be positive/absolute value "
                        "per §5.2), or missing OI&E component line."
                    ),
                    "section": "other_income",
                    "computed_value": round(oie_sum, 2),
                    "reported_value": round(total_oie, 2),
                    "discrepancy": round(discrepancy, 2),
                })

        # --- Check 5 (X10): Net Income ÷ Basic Shares ≈ Basic EPS ---
        net_income = _get(period, "Net Income (Loss)")
        basic_shares = _get(period, "Wtd. Avg. Shares — Basic (MM)")
        basic_eps = _get(period, "EPS — Basic ($)")

        if net_income is not None and basic_shares is not None and basic_eps is not None and basic_shares > 0:
            implied_eps = net_income / basic_shares
            discrepancy = abs(implied_eps - basic_eps)
            if discrepancy > 0.02:
                failures.append({
                    "check_id": "eps_plausibility",
                    "period_label": label,
                    "period_index": i,
                    "description": (
                        f"Net Income ÷ Basic Shares ≠ Basic EPS within $0.02. "
                        f"Implied EPS={implied_eps:.4f}, Reported EPS={basic_eps:.4f}, "
                        f"discrepancy={discrepancy:.4f}. "
                        "Check Net Income units (should be millions) or share count."
                    ),
                    "section": "per_share",
                    "computed_value": round(implied_eps, 4),
                    "reported_value": round(basic_eps, 4),
                    "discrepancy": round(discrepancy, 4),
                })

    return failures


def finalize_extraction(
    base_fields: Any,
    corrections: Any,
) -> list[dict[str, Any]]:
    """
    Apply re_extract corrections over the base extracted data.

    When *corrections* is empty (no cross-check failures), the base data is
    returned unchanged. Otherwise, the corrections list is expected to be a
    list of period dicts in the same order as *base_fields* — field values
    present in corrections override those in base_fields.

    Args:
        base_fields:  List of period dicts from compute_derived_fields.
        corrections:  List of period dicts from re_extract llm_job, or empty
                      list / JSON string thereof when no failures occurred.

    Returns:
        Final list of period dicts to store.
    """
    base = _coerce_list_of_dicts(base_fields)
    corr = _coerce_list_of_dicts(corrections)

    if not corr:
        return base

    result: list[dict[str, Any]] = []
    for i, base_period in enumerate(base):
        out = dict(base_period)
        if i < len(corr) and isinstance(corr[i], dict):
            for k, v in corr[i].items():
                if v != _FIELD_NOT_FOUND:
                    out[k] = v
        result.append(out)
    return result


def zip_pages_with_periods(
    pages_list: Any,
    periods: Any,
) -> list[dict[str, Any]]:
    """
    Zip a parallel_over select output with the corresponding periods list.

    When a ``select`` task runs ``parallel_over: "{{session.filings}}"``, its
    output is ``[PageList_0, PageList_1, PageList_2]`` — one page set per
    filing, in the same order as ``session.periods``.  Passing this list
    directly to a subsequent ``extract_fields`` task causes all three
    extraction calls to receive the *concatenated* text from all filings,
    producing identical values in every column.

    This function zips the two parallel lists into::

        [
            {"pages": PageList_0, "period_end": "2024-12-31"},
            {"pages": PageList_1, "period_end": "2024-03-31"},
            {"pages": PageList_2, "period_end": "2025-03-31"},
        ]

    The extraction task can then ``parallel_over`` this zipped list and use
    ``{{item.pages}}`` as ``document`` and ``{{item.period_end}}`` as
    ``period_end``, giving each extraction call exactly one filing's pages.

    Args:
        pages_list: List of page sets produced by a ``select`` parallel task.
                    Each element may be a PageList, DocumentHandle, or any
                    value that ``extract_fields`` accepts as a document.
        periods:    List of period objects (PeriodDescriptor or dict) ordered
                    identically to ``pages_list``.  Must have ``period_end``
                    as either an attribute or a dict key.

    Returns:
        List of dicts, one per paired item, with keys ``pages`` and
        ``period_end``.
    """
    if not isinstance(pages_list, list):
        pages_list = list(pages_list)
    if not isinstance(periods, list):
        periods = list(periods)

    result: list[dict[str, Any]] = []
    for pages, period in zip(pages_list, periods):
        if hasattr(period, "period_end"):
            period_end = period.period_end
        elif isinstance(period, dict):
            period_end = period.get("period_end", "")
        else:
            period_end = str(period)
        result.append({"pages": pages, "period_end": period_end})
    return result


# ---------------------------------------------------------------------------
# Default registry construction
# ---------------------------------------------------------------------------


def build_finance_registry() -> FunctionRegistry:
    """
    Return a FunctionRegistry pre-populated with the canonical finance functions.

    This is called at application startup to make the default functions
    available to all ``compute`` tasks.
    """
    registry = FunctionRegistry()

    registry.register(RegisteredFunction(
        name="fiscal_period_logic",
        fn=fiscal_period_logic,
        input_schema={"as_of_date": "str", "company": "str"},
        output_schema="list[PeriodDescriptor]",
        description=(
            "Returns 1 or 3 PeriodDescriptors depending on whether as_of_date "
            "is a fiscal year-end. Annual filing date → single annual descriptor. "
            "Interim date → annual (last FYE), ytd_current, ytd_prior."
        ),
    ))

    registry.register(RegisteredFunction(
        name="ticker_lookup",
        fn=ticker_lookup,
        input_schema={"company": "str"},
        output_schema="str",
        description="Resolves a company name to its primary exchange ticker symbol.",
    ))

    registry.register(RegisteredFunction(
        name="financial_scale_normalize",
        fn=financial_scale_normalize,
        input_schema={
            "value": "number",
            "source_currency": "str",
            "target_currency": "str",
            "target_scale": "str",
        },
        output_schema="float",
        description=(
            "Converts a financial value between currency and scale units. "
            "E.g. 1,000,000 USD ones → 1.0 USD millions."
        ),
    ))

    registry.register(RegisteredFunction(
        name="period_label",
        fn=period_label,
        input_schema={"date": "str", "period_type": "str"},
        output_schema="str",
        description=(
            "Produces a standardised period label from a date and period type. "
            "E.g. date=2025-03-31, period_type=quarterly → 'Q1 2025'."
        ),
    ))

    registry.register(RegisteredFunction(
        name="fiscal_year_end",
        fn=fiscal_year_end,
        input_schema={"company": "str"},
        output_schema="str",
        description=(
            "Returns the fiscal year-end as MM-DD for the given company. "
            "E.g. 'apple' → '09-30'."
        ),
    ))

    registry.register(RegisteredFunction(
        name="calculate_derived_fields",
        fn=calculate_derived_fields,
        input_schema={"extracted_fields": "list[dict]"},
        output_schema="list[dict]",
        description=(
            "Deterministically computes derived financial metrics for each period dict: "
            "gross_profit, gross_margin_pct, operating_margin_pct, net_margin_pct, "
            "effective_tax_rate. Replaces unreliable LLM extraction of calculated rows."
        ),
    ))

    registry.register(RegisteredFunction(
        name="apply_segment_names",
        fn=apply_segment_names,
        input_schema={
            "extracted_fields": "list[dict]",
            "segment_names": "dict",
        },
        output_schema="list[dict]",
        description=(
            "Renames placeholder segment-index keys (e.g. revenue_segment_[1]) "
            "to resolved segment names (e.g. revenue_segment_google_services) "
            "using the segment_names mapping from generate_analyst_notes."
        ),
    ))

    registry.register(RegisteredFunction(
        name="zip_pages_with_periods",
        fn=zip_pages_with_periods,
        input_schema={
            "pages_list": "list",
            "periods": "list",
        },
        output_schema="list[dict]",
        description=(
            "Zips a parallel select output (list of page sets, one per filing) with the "
            "corresponding periods list into [{pages, period_end}, ...] pairs. "
            "Use between a parallel select step and a parallel extract step to route each "
            "extraction call to its own filing's pages rather than the concatenated whole."
        ),
    ))

    registry.register(RegisteredFunction(
        name="assemble_extraction_sections",
        fn=assemble_extraction_sections,
        input_schema={
            "face": "list[dict]",
            "segments": "list[dict]",
            "other_income": "list[dict]",
            "per_share": "list[dict]",
        },
        output_schema="list[dict]",
        description=(
            "Merges four section-specific extraction outputs (face, segments, "
            "other_income, per_share) into unified per-period dicts by positional "
            "alignment. Each input list must be ordered identically (same periods)."
        ),
    ))

    registry.register(RegisteredFunction(
        name="compute_derived_fields",
        fn=compute_derived_fields,
        input_schema={
            "extracted_fields": "list[dict]",
            "periods": "list",
        },
        output_schema="list[dict]",
        description=(
            "Deterministically computes all derived financial metrics using exact "
            "template field names: Gross Profit, Gross Margin (%), Operating Margin (%), "
            "Net Margin (%), Effective Tax Rate (%), Total Operating Expenses, YoY Growth (%). "
            "Replaces LLM extraction of calculated rows."
        ),
    ))

    registry.register(RegisteredFunction(
        name="validate_cross_checks",
        fn=validate_cross_checks,
        input_schema={
            "extracted_fields": "list[dict]",
            "periods": "list",
        },
        output_schema="list[dict]",
        description=(
            "Runs 4 deterministic arithmetic cross-checks per manual §§2.3, 3.5, 5, 9.2: "
            "(1) segment revenue sum vs Total Revenues, (2) Total OpEx reconciliation, "
            "(3) segment OI sum vs Operating Income, (4) EPS plausibility. "
            "Returns a list of failure dicts (period, check, delta, fields) for any checks "
            "that fail. Empty list means all checks passed."
        ),
    ))

    registry.register(RegisteredFunction(
        name="finalize_extraction",
        fn=finalize_extraction,
        input_schema={
            "base_fields": "list[dict]",
            "corrections": "list[dict]",
        },
        output_schema="list[dict]",
        description=(
            "Applies re_extract corrections over the base extracted data. "
            "If corrections is empty or None, returns base_fields unchanged. "
            "Otherwise merges corrections into base_fields period-by-period, "
            "skipping __not_found__ values."
        ),
    ))

    return registry


#: Module-level default registry instance. Import and use directly, or
#: call build_finance_registry() to create an isolated instance for testing.
default_finance_registry: FunctionRegistry = build_finance_registry()
