"""Canonical finance function implementations.

These are the built-in registered functions for the finance domain. Each
function is deterministic and does not require an LLM invocation. They are
registered in a shared default FunctionRegistry at application startup.

Functions
---------
fiscal_period_logic     — Resolve fiscal periods for a given date and company
ticker_lookup           — Resolve a company name to its primary exchange ticker
financial_scale_normalize — Convert a financial value between currency/scale
period_label            — Produce a standardised period label string
fiscal_year_end         — Return the fiscal year-end month/day for a company
"""

from __future__ import annotations

import datetime
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

    return registry


#: Module-level default registry instance. Import and use directly, or
#: call build_finance_registry() to create an isolated instance for testing.
default_finance_registry: FunctionRegistry = build_finance_registry()
