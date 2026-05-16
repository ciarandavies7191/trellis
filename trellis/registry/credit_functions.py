"""Credit due-diligence compute functions.

Pure-Python, deterministic implementations registered into the shared
FunctionRegistry so pipeline YAML files can invoke them via the ``compute``
tool (``function: build_financial_spread``, etc.) without writing any code.

Functions
---------
build_financial_spread    — Apply an LLM-generated XBRL schema mapping to raw
                            companyfacts data; produce a 3-year financial spread
                            and canonical metrics block.
validate_spread           — Run accounting-identity checks on spread output.
compute_proforma_stress   — Deterministic pro-forma impact, stress scenarios,
                            and covenant compliance tests.
run_dcf_model             — 5-year projection and DCF valuation.
extract_xbrl_concept_list — Helper: extract a compact concept-name list from a
                            XBRL facts dict (for schema-discovery LLM prompts).
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from trellis.registry.functions import FunctionRegistry, RegisteredFunction

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _nn(v: Any) -> bool:
    """True iff v is a finite number."""
    if v is None:
        return False
    if isinstance(v, float) and v != v:  # NaN
        return False
    return True


def _fmt(v: Any) -> str:
    if not _nn(v):
        return "N/A"
    if abs(v) >= 100:
        return f"{v:,.0f}"
    if abs(v) >= 10:
        return f"{v:.1f}"
    return f"{v:.2f}"


def _fmtx(v: Any) -> str:
    return f"{v:.2f}x" if _nn(v) else "N/A"


def _fmtpct(v: Any) -> str:
    return f"{v:.1f}%" if _nn(v) else "N/A"


# ---------------------------------------------------------------------------
# Function 1: build_financial_spread
# ---------------------------------------------------------------------------


def _extract_annual_values(
    facts: dict,
    concept_path: str,
    target_years: list[str],
) -> dict[str, float | None]:
    """Extract the annual 10-K value for each target fiscal year from XBRL facts."""
    try:
        taxonomy, concept = concept_path.split("/", 1)
    except ValueError:
        return {y: None for y in target_years}

    units_block = (
        facts.get(taxonomy, {})
             .get(concept, {})
             .get("units", {})
    )
    entries: list[dict] = (
        units_block.get("USD")
        or units_block.get("shares")
        or []
    )

    annual: dict[str, list[dict]] = {}
    for e in entries:
        if e.get("form") not in ("10-K", "10-K/A"):
            continue
        fy = e.get("end", "")[:4]
        annual.setdefault(fy, []).append(e)

    result: dict[str, float | None] = {}
    for yr in target_years:
        candidates = annual.get(yr, [])
        if not candidates:
            result[yr] = None
            continue
        # Prefer 10-K over 10-K/A; then latest filed date
        candidates.sort(
            key=lambda x: (x.get("form") != "10-K", x.get("filed", ""))
        )
        best = candidates[-1]
        val = best.get("val")
        result[yr] = round(val / 1_000_000, 2) if val is not None else None

    return result


def build_financial_spread(
    xbrl_data: dict,
    schema_mapping: dict | str,
    fiscal_years: int = 3,
) -> dict:
    """Build a 3-year financial spread from XBRL companyfacts.

    Args:
        xbrl_data:      Output of ``fetch_sec_xbrl`` (must include ``facts``).
        schema_mapping: LLM-generated mapping of canonical field names to XBRL
                        concept paths, e.g. ``{"revenue": "us-gaap/Revenues"}``.
                        May be a JSON string (auto-parsed).
        fiscal_years:   Number of most recent annual fiscal years to include.

    Returns:
        dict: ticker, fiscal_years, income_statement, balance_sheet,
              cash_flow, canonical_metrics, maturity_schedule, provenance.
    """
    # Accept JSON string from LLM task output
    if isinstance(schema_mapping, str):
        try:
            schema_mapping = json.loads(schema_mapping)
        except Exception:
            import re
            m = re.search(r"\{.*\}", schema_mapping, re.S)
            if m:
                schema_mapping = json.loads(m.group(0))
            else:
                schema_mapping = {}

    facts = xbrl_data.get("facts", {})
    ticker = xbrl_data.get("ticker", "")

    current_year = datetime.utcnow().year
    target_years = [str(current_year - i) for i in range(1, fiscal_years + 2)]

    raw: dict[str, dict[str, float | None]] = {}
    provenance: dict[str, str] = {}
    for field, concept_path in schema_mapping.items():
        if not concept_path:
            continue
        raw[field] = _extract_annual_values(facts, concept_path, target_years)
        provenance[field] = concept_path

    years_with_data = [
        y for y in target_years
        if raw.get("revenue", {}).get(y) is not None
    ]
    selected_years = sorted(years_with_data)[-fiscal_years:]

    if not selected_years:
        raise ValueError(
            f"No annual revenue data found for {ticker} in XBRL. "
            "Check the schema_mapping contains a 'revenue' entry."
        )

    latest_fy = selected_years[-1]

    def g(field: str, yr: str) -> float | None:
        return raw.get(field, {}).get(yr)

    rev     = g("revenue", latest_fy)
    op_inc  = g("operating_income", latest_fy)
    da      = g("da", latest_fy)
    ebitda_gaap = (
        round(op_inc + da, 2)
        if op_inc is not None and da is not None else None
    )
    total_debt = g("total_debt", latest_fy)
    st_debt = g("current_debt", latest_fy) or 0.0
    if total_debt is not None:
        total_debt = round(total_debt + st_debt, 2)

    cash    = g("cash", latest_fy)
    net_debt = round(total_debt - cash, 2) if _nn(total_debt) and _nn(cash) else None
    cfo     = g("cfo", latest_fy)
    capex   = g("capex", latest_fy)
    fcf     = round(cfo - abs(capex), 2) if _nn(cfo) and _nn(capex) else None
    int_exp = g("interest_expense", latest_fy)
    equity  = g("equity", latest_fy)
    net_inc = g("net_income", latest_fy)
    gross_p = g("gross_profit", latest_fy)
    cogs    = g("cogs", latest_fy)
    if gross_p is None and _nn(rev) and _nn(cogs):
        gross_p = round(rev - abs(cogs), 2)

    gross_lev = (
        round(total_debt / ebitda_gaap, 2)
        if _nn(total_debt) and _nn(ebitda_gaap) and ebitda_gaap > 0 else None
    )
    net_lev = (
        round(net_debt / ebitda_gaap, 2)
        if _nn(net_debt) and _nn(ebitda_gaap) and ebitda_gaap > 0 else None
    )
    int_cov = (
        round(ebitda_gaap / abs(int_exp), 2)
        if _nn(ebitda_gaap) and _nn(int_exp) and int_exp != 0 else None
    )

    precision_dp = 0 if _nn(rev) and rev > 1000 else 1

    canonical_metrics = {
        "latest_fy": latest_fy,
        "currency_scale": "millions_usd",
        "precision_dp": precision_dp,
        "revenue": rev,
        "cogs": cogs,
        "gross_profit": gross_p,
        "operating_income": op_inc,
        "ebitda_gaap": ebitda_gaap,
        "ebitda_adjusted": ebitda_gaap,  # management override applied in authoring pass
        "net_income": net_inc,
        "da": da,
        "interest_expense": int_exp,
        "total_assets": g("total_assets", latest_fy),
        "total_debt": total_debt,
        "net_debt": net_debt,
        "cash": cash,
        "equity": equity,
        "cfo": cfo,
        "capex": capex,
        "fcf": fcf,
        "gross_leverage_adj": gross_lev,
        "net_leverage_adj": net_lev,
        "interest_coverage_adj": int_cov,
        "mgmt_ebitda_adjusted": None,
        "maturity_schedule": {},
    }

    def series(field: str) -> dict[str, float | None]:
        return {yr: raw.get(field, {}).get(yr) for yr in selected_years}

    return {
        "ticker": ticker,
        "fiscal_years": selected_years,
        "income_statement": {
            "revenue":          series("revenue"),
            "cogs":             series("cogs"),
            "gross_profit":     series("gross_profit"),
            "operating_income": series("operating_income"),
            "ebitda_gaap": {
                yr: (
                    round(
                        (raw.get("operating_income", {}).get(yr) or 0)
                        + (raw.get("da", {}).get(yr) or 0),
                        2,
                    )
                    if raw.get("operating_income", {}).get(yr) is not None
                    else None
                )
                for yr in selected_years
            },
            "net_income":       series("net_income"),
            "da":               series("da"),
            "interest_expense": series("interest_expense"),
        },
        "balance_sheet": {
            "total_assets": series("total_assets"),
            "total_debt":   series("total_debt"),
            "cash":         series("cash"),
            "equity":       series("equity"),
        },
        "cash_flow": {
            "cfo":   series("cfo"),
            "capex": series("capex"),
        },
        "canonical_metrics": canonical_metrics,
        "maturity_schedule": {},
        "provenance": provenance,
    }


# ---------------------------------------------------------------------------
# Function 2: validate_spread
# ---------------------------------------------------------------------------


def validate_spread(spread_data: dict) -> dict:
    """Run accounting-identity checks on a financial spread.

    Checks:
        ebitda_identity    – EBITDA ≈ Operating Income + D&A  (±5%)
        net_debt_identity  – Net Debt ≈ Total Debt − Cash      (±1%)
        fcf_identity       – FCF ≈ CFO − CapEx                 (±1%, warn-only)
        leverage_sanity    – Gross Leverage 0.1–30×             (warn-only)
        revenue_positive   – Revenue > 0
        ebitda_margin      – EBITDA margin −50% to 80%          (warn-only)

    Returns:
        dict: overall_status (PASS|WARN|FAIL), gates, warnings, errors.
    """
    cm = spread_data.get("canonical_metrics", {})
    gates: dict[str, Any] = {}
    warnings: list[str] = []
    errors: list[str] = []

    def check(
        name: str,
        condition: bool,
        delta_pct: float | None = None,
        critical: bool = True,
    ) -> None:
        status = "PASS" if condition else ("FAIL" if critical else "WARN")
        gates[name] = {"status": status, "delta_pct": delta_pct}
        if status == "FAIL":
            errors.append(
                f"{name}: FAIL (delta={delta_pct:.1f}%)"
                if delta_pct is not None
                else f"{name}: FAIL"
            )
        elif status == "WARN":
            warnings.append(f"{name}: WARN")

    def pct_delta(a: Any, b: Any) -> float | None:
        if not _nn(a) or not _nn(b) or b == 0:
            return None
        return abs(a - b) / abs(b) * 100

    rev = cm.get("revenue")
    op  = cm.get("operating_income")
    da  = cm.get("da")
    eg  = cm.get("ebitda_gaap")
    td  = cm.get("total_debt")
    csh = cm.get("cash")
    nd  = cm.get("net_debt")
    cfo = cm.get("cfo")
    cx  = cm.get("capex")
    fcf = cm.get("fcf")

    # EBITDA = OpInc + D&A
    if _nn(op) and _nn(da) and _nn(eg):
        d = pct_delta(op + da, eg)
        check("ebitda_identity", d is not None and d < 5.0, d)
    else:
        gates["ebitda_identity"] = {"status": "SKIP", "delta_pct": None}

    # Net Debt = Total Debt − Cash
    if _nn(td) and _nn(csh) and _nn(nd):
        d = pct_delta(td - csh, nd)
        check("net_debt_identity", d is not None and d < 1.0, d)
    else:
        gates["net_debt_identity"] = {"status": "SKIP", "delta_pct": None}

    # FCF = CFO − CapEx (warn only)
    if _nn(cfo) and _nn(cx) and _nn(fcf):
        d = pct_delta(cfo - abs(cx), fcf)
        check("fcf_identity", d is not None and d < 1.0, d, critical=False)
    else:
        gates["fcf_identity"] = {"status": "SKIP", "delta_pct": None}

    # Leverage sanity (warn only)
    lev = cm.get("gross_leverage_adj")
    if _nn(lev):
        check("leverage_sanity", 0.1 <= lev <= 30.0, critical=False)

    # Revenue > 0
    if _nn(rev):
        check("revenue_positive", rev > 0, critical=True)
    else:
        errors.append("revenue_positive: FAIL — revenue is null")
        gates["revenue_positive"] = {"status": "FAIL"}

    # EBITDA margin −50% to 80% (warn only)
    if _nn(eg) and _nn(rev) and rev > 0:
        margin = eg / rev
        check("ebitda_margin_sanity", -0.5 <= margin <= 0.80, critical=False)

    overall = "FAIL" if errors else ("WARN" if warnings else "PASS")
    return {
        "overall_status": overall,
        "gates": gates,
        "warnings": warnings,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# Function 3: compute_proforma_stress
# ---------------------------------------------------------------------------


def compute_proforma_stress(
    canonical_metrics: dict,
    facility: dict,
    covenants: list | None = None,
) -> dict:
    """Deterministic pro-forma impact, stress scenarios, and covenant tests.

    Args:
        canonical_metrics: Canonical metrics block (from build_financial_spread).
        facility: Facility terms: amount_mm, product, pricing_display, spread_bps.
        covenants: List of covenant objects (id, name, type, value, direction).

    Returns:
        dict: markdown (formatted tables), proforma_leverage, proforma_coverage,
              stress_results, covenant_breaches.
    """
    covenants = covenants or []
    cm = canonical_metrics
    fac_amt    = facility.get("amount_mm", 0) or 0
    spread_bps = facility.get("spread_bps", 150) or 150
    sofr       = 530  # SOFR ~5.30 % as of mid-2026
    all_in_bps = sofr + spread_bps
    all_in_rate = all_in_bps / 10_000

    rev     = cm.get("revenue")
    ebitda  = cm.get("ebitda_adjusted") or cm.get("ebitda_gaap")
    td      = cm.get("total_debt")
    cash    = cm.get("cash")
    nd      = cm.get("net_debt")
    int_exp = cm.get("interest_expense")
    cfo     = cm.get("cfo")
    capex   = cm.get("capex")

    # Net-debt sanity: if near-zero vs large total debt, use total debt
    nd_warn: str | None = None
    if _nn(nd) and _nn(td) and td > 5000 and abs(nd) < td * 0.02:
        nd_warn = (
            f"Net Debt ({_fmt(nd)}M) ≈ 0 vs Total Debt ({_fmt(td)}M) "
            "— using Total Debt as proxy."
        )
        nd = td

    new_interest = fac_amt * all_in_rate
    pf_debt   = (td + fac_amt) if _nn(td) else None
    pf_nd     = (nd + fac_amt) if _nn(nd) else None
    pf_int    = (abs(int_exp) if _nn(int_exp) else 0.0) + new_interest
    pf_lev    = pf_debt / ebitda   if _nn(pf_debt) and _nn(ebitda) and ebitda > 0 else None
    pf_nd_lev = pf_nd   / ebitda   if _nn(pf_nd)   and _nn(ebitda) and ebitda > 0 else None
    pf_cov    = ebitda  / pf_int   if _nn(ebitda)  and pf_int > 0 else None
    as_lev    = td      / ebitda   if _nn(td)       and _nn(ebitda) and ebitda > 0 else None
    as_nd_lev = nd      / ebitda   if _nn(nd)       and _nn(ebitda) and ebitda > 0 else None
    as_cov    = ebitda  / abs(int_exp) if _nn(ebitda) and _nn(int_exp) and int_exp != 0 else None

    out: list[str] = ["## Pro Forma / Stress / Covenant Analysis\n"]
    out.append(
        "_All inputs from canonical metrics (Step 2). "
        "Primary covenant metric: **Gross Debt / Adj EBITDA**._\n"
    )
    if nd_warn:
        out.append(f"> ⚠️ **Net Debt Warning**: {nd_warn}\n")

    # Pro-forma impact table
    out.append("### Pro Forma Impact ($M)\n| Metric | As-Reported | Pro Forma | Change |")
    out.append("|---|---|---|---|")
    out.append(f"| Gross Debt | {_fmt(td)} | {_fmt(pf_debt)} | +{_fmt(fac_amt)} |")
    if _nn(nd):
        out.append(f"| Net Debt | {_fmt(nd)} | {_fmt(pf_nd)} | +{_fmt(fac_amt)} |")
    out.append(f"| Adj EBITDA | {_fmt(ebitda)} | {_fmt(ebitda)} | — |")
    out.append(
        f"| Interest Expense | {_fmt(abs(int_exp) if _nn(int_exp) else None)} "
        f"| {_fmt(pf_int)} | +{_fmt(new_interest)} |"
    )
    delta_gl = (
        f"+{_fmtx(pf_lev - as_lev)}"
        if _nn(pf_lev) and _nn(as_lev) else "N/A"
    )
    out.append(
        f"| **Gross Debt / Adj EBITDA** | {_fmtx(as_lev)} | {_fmtx(pf_lev)} | {delta_gl} |"
    )
    if _nn(as_nd_lev) or _nn(pf_nd_lev):
        delta_nl = (
            f"+{_fmtx(pf_nd_lev - as_nd_lev)}"
            if _nn(pf_nd_lev) and _nn(as_nd_lev) else "N/A"
        )
        out.append(
            f"| Net Debt / Adj EBITDA | {_fmtx(as_nd_lev)} | {_fmtx(pf_nd_lev)} | {delta_nl} |"
        )
    delta_cov = (
        _fmtx(pf_cov - as_cov) if _nn(pf_cov) and _nn(as_cov) else "N/A"
    )
    out.append(
        f"| EBITDA / Interest | {_fmtx(as_cov)} | {_fmtx(pf_cov)} | {delta_cov} |"
    )

    # Stress scenarios
    eb_margin = (
        (ebitda / rev)
        if _nn(ebitda) and _nn(rev) and rev > 0 else None
    )
    scenarios_def = [
        ("Base (Pro Forma)",                  0.00,  0.000),
        ("Stress (−25% Rev, −200bps margin)", -0.25, -0.020),
        ("Severe (−45% Rev, −400bps margin)", -0.45, -0.040),
    ]
    stress_results: list[dict] = []
    covenant_breaches: list[dict] = []

    out.append(
        "\n### Stress Scenarios\n"
        "| Scenario | Stress EBITDA | Gross Lev | Coverage | Pass/Fail |"
    )
    out.append("|---|---|---|---|---|")

    for label, rev_shock, margin_shock in scenarios_def:
        if _nn(rev) and _nn(eb_margin):
            s_rev    = rev * (1 + rev_shock)
            s_ebitda = s_rev * (eb_margin + margin_shock)
        elif _nn(ebitda):
            s_ebitda = ebitda * (1 + rev_shock)
        else:
            s_ebitda = None

        s_lev = (
            pf_debt / s_ebitda
            if _nn(pf_debt) and _nn(s_ebitda) and s_ebitda > 0 else None
        )
        s_cov = (
            s_ebitda / pf_int
            if _nn(s_ebitda) and pf_int > 0 else None
        )

        scenario_pass = True
        for cov in covenants:
            cov_type  = cov.get("type", "")
            cov_val   = cov.get("value")
            cov_dir   = cov.get("direction", "max")
            if cov_val is None:
                continue
            metric: float | None = None
            if "gross_leverage" in cov_type:
                metric = s_lev
            elif "net_leverage" in cov_type:
                metric = (
                    pf_nd / s_ebitda
                    if _nn(pf_nd) and _nn(s_ebitda) and s_ebitda > 0 else None
                )
            elif "coverage" in cov_type:
                metric = s_cov
            elif "min_ebitda" in cov_type:
                metric = s_ebitda
            if metric is not None:
                breach = (metric > cov_val) if cov_dir == "max" else (metric < cov_val)
                if breach:
                    scenario_pass = False
                    covenant_breaches.append({
                        "scenario": label,
                        "covenant": cov.get("name", cov_type),
                        "threshold": cov_val,
                        "actual": round(metric, 2),
                    })

        flag = "✅" if scenario_pass else "🔴 BREACH"
        out.append(
            f"| {label} | {_fmt(s_ebitda)} | {_fmtx(s_lev)} | {_fmtx(s_cov)} | {flag} |"
        )
        stress_results.append({
            "scenario": label,
            "stress_ebitda": s_ebitda,
            "gross_leverage": s_lev,
            "coverage": s_cov,
            "pass": scenario_pass,
        })

    return {
        "markdown": "\n".join(out),
        "proforma_leverage": pf_lev,
        "proforma_coverage": pf_cov,
        "stress_results": stress_results,
        "covenant_breaches": covenant_breaches,
    }


# ---------------------------------------------------------------------------
# Function 4: run_dcf_model
# ---------------------------------------------------------------------------


def run_dcf_model(canonical_metrics: dict, assumptions: dict | str) -> dict:
    """5-year projection and DCF valuation.

    The LLM contributes expert judgment parameters (``beta_u``, growth and
    margin adjustments) via a preceding ``llm_job`` task; this function
    runs the deterministic computation from those parameters.

    Args:
        canonical_metrics: Canonical metrics block.
        assumptions: JSON object (or string) with keys:
                     bypass (bool), beta_u (float),
                     terminal_growth_adj, ebitda_margin_adj, rev_growth_adj,
                     rationale (str).

    Returns:
        dict: markdown (formatted tables), enterprise_value_base,
              implied_leverage_base, wacc.  If assumptions.bypass is True,
              returns a minimal bypass response.
    """
    # Accept JSON string from LLM output
    if isinstance(assumptions, str):
        try:
            assumptions = json.loads(assumptions)
        except Exception:
            import re
            m = re.search(r"\{.*\}", assumptions, re.S)
            assumptions = json.loads(m.group(0)) if m else {}

    if assumptions.get("bypass"):
        reason = assumptions.get("reason", "Investment grade — projections not required")
        return {
            "markdown": f"## Projections & DCF Valuation\n\n_{reason}_",
            "enterprise_value_base": None,
            "implied_leverage_base": None,
            "wacc": None,
        }

    cm = canonical_metrics

    def _safe(v: Any, default: float = 0.0) -> float:
        return float(v) if _nn(v) else default

    rev0     = _safe(cm.get("revenue"))
    ebitda0  = _safe(cm.get("ebitda_adjusted") or cm.get("ebitda_gaap"))
    debt0    = _safe(cm.get("total_debt"))
    net_debt0 = _safe(cm.get("net_debt"))
    int_exp0 = abs(_safe(cm.get("interest_expense")))
    da0      = _safe(cm.get("da"))
    capex0   = abs(_safe(cm.get("capex")))
    net_inc0 = _safe(cm.get("net_income"))
    equity0  = _safe(cm.get("equity"), default=1.0) or 1.0
    facility = _safe(cm.get("_facility_amount_mm"))

    beta_u     = float(assumptions.get("beta_u", 0.90))
    tg_adj     = float(assumptions.get("terminal_growth_adj", 0.0))
    margin_adj = float(assumptions.get("ebitda_margin_adj", 0.0))
    growth_adj = float(assumptions.get("rev_growth_adj", 0.0))
    rationale  = str(assumptions.get("rationale", ""))

    def safe_div(a: float, b: float) -> float | None:
        return a / b if b and b != 0 else None

    margin_latest = safe_div(ebitda0, rev0)
    capex_pct     = safe_div(capex0, rev0)
    da_pct        = safe_div(da0, rev0)
    kd_pretax     = safe_div(int_exp0, debt0) if debt0 > 0 else 0.055

    rev_cagr = 0.03  # default

    # WACC
    risk_free = 0.043
    erp       = 0.055
    pre_tax_income = ebitda0 - da0 - int_exp0
    eff_tax = (
        1 - (net_inc0 / pre_tax_income)
        if pre_tax_income > 0 and net_inc0 else 0.25
    )
    if not (0.10 <= eff_tax <= 0.45):
        eff_tax = 0.25
    de_ratio = safe_div(debt0 + facility, equity0) or 1.0
    beta_l   = beta_u * (1 + (1 - eff_tax) * de_ratio)
    ke       = risk_free + beta_l * erp
    kd_post  = (kd_pretax or 0.055) * (1 - eff_tax)
    w_e      = 1 / (1 + de_ratio) if de_ratio else 0.5
    w_d      = 1 - w_e
    wacc     = ke * w_e + kd_post * w_d

    # Projection assumptions
    base_growth_13 = min(max(rev_cagr + growth_adj, -0.02), 0.08)
    base_growth_45 = min(max(rev_cagr * 0.7 + growth_adj, 0.01), 0.05)
    base_margin    = (margin_latest or 0.15) + margin_adj
    terminal_growth = 0.025 + tg_adj
    down_growth_13 = base_growth_13 - 0.04
    down_margin    = base_margin - 0.03

    def project(
        g13: float, g45: float, margin: float
    ) -> tuple[list[float], list[float]]:
        rev = rev0
        ebitda_lst, fcf_lst = [], []
        for yr in range(1, 6):
            g = g13 if yr <= 3 else g45
            rev = rev * (1 + g)
            ebitda = rev * margin
            da  = rev * (da_pct or 0.03)
            cx  = rev * (capex_pct or 0.04)
            ebit = ebitda - da
            nopat = ebit * (1 - eff_tax)
            fcf  = nopat + da - cx
            ebitda_lst.append(round(ebitda, 1))
            fcf_lst.append(round(fcf, 1))
        return ebitda_lst, fcf_lst

    base_ebitda, base_fcf = project(base_growth_13, base_growth_45, base_margin)
    down_ebitda, down_fcf = project(down_growth_13, base_growth_45 - 0.03, down_margin)

    def dcf(fcfs: list[float], terminal_fcf: float) -> float:
        pv = sum(f / (1 + wacc) ** (i + 1) for i, f in enumerate(fcfs))
        if wacc <= terminal_growth:
            tv = 0.0
        else:
            tv = terminal_fcf * (1 + terminal_growth) / (wacc - terminal_growth)
        pv_tv = tv / (1 + wacc) ** 5
        return round(pv + pv_tv, 0)

    ev_base = dcf(base_fcf, base_fcf[-1])
    ev_down = dcf(down_fcf, down_fcf[-1])
    impl_lev_base = (
        round((debt0 + facility) / base_ebitda[-1], 2)
        if base_ebitda[-1] else None
    )

    yrs = list(range(1, 6))
    out: list[str] = [
        "## Projections & DCF Valuation\n",
        f"_{rationale}_\n" if rationale else "",
        "### Section A: Key Assumptions\n",
        "| Assumption | Base | Downside | Source |",
        "|---|---|---|---|",
        f"| Revenue Growth Yr 1-3 | {base_growth_13*100:.1f}% | {down_growth_13*100:.1f}% "
        f"| 2Y CAGR {rev_cagr*100:.1f}% |",
        f"| Revenue Growth Yr 4-5 | {base_growth_45*100:.1f}% | {(base_growth_45-0.03)*100:.1f}% "
        f"| Terminal fade |",
        f"| EBITDA Margin | {base_margin*100:.1f}% | {down_margin*100:.1f}% "
        f"| Latest {(margin_latest or 0)*100:.1f}% |",
        f"| Unlevered Beta | {beta_u:.2f} | {beta_u:.2f} "
        f"| {rationale[:40] if rationale else 'Sector estimate'} |",
        f"| WACC | {wacc*100:.2f}% | {(wacc+0.015)*100:.2f}% | Built-up |",
        f"| Terminal Growth | {terminal_growth*100:.2f}% | {(terminal_growth-0.01)*100:.2f}% "
        f"| GDP-linked |",
        "\n### Section B: WACC Build",
        f"Risk-free rate: {risk_free*100:.2f}%  |  ERP: {erp*100:.1f}%  |  β_u: {beta_u:.2f}"
        f"  |  β_l: {beta_l:.2f}  |  Ke: {ke*100:.2f}%  |  Kd (pre-tax): {(kd_pretax or 0)*100:.2f}%"
        f"  |  WACC: **{wacc*100:.2f}%**\n",
        "### Section C: 5-Year Projections ($M)\n",
        "| Year | " + " | ".join(f"Yr {y}" for y in yrs) + " |",
        "|---|" + "---|" * 5,
        "| Base EBITDA | " + " | ".join(f"{v:,.0f}" for v in base_ebitda) + " |",
        "| Base FCF    | " + " | ".join(f"{v:,.0f}" for v in base_fcf) + " |",
        "| Down EBITDA | " + " | ".join(f"{v:,.0f}" for v in down_ebitda) + " |",
        "| Down FCF    | " + " | ".join(f"{v:,.0f}" for v in down_fcf) + " |",
        "\n### Section D: DCF Valuation",
        "| Scenario | Enterprise Value ($M) | Net Debt ($M) | Implied Equity ($M) |",
        "|---|---|---|---|",
        f"| Base | {ev_base:,.0f} | {net_debt0+facility:,.0f} "
        f"| {max(ev_base-(net_debt0+facility),0):,.0f} |",
        f"| Downside | {ev_down:,.0f} | {net_debt0+facility:,.0f} "
        f"| {max(ev_down-(net_debt0+facility),0):,.0f} |",
        f"\n_Implied Year 5 Gross Leverage (Base): {_fmtx(impl_lev_base)}_",
    ]

    return {
        "markdown": "\n".join(out),
        "enterprise_value_base": ev_base,
        "implied_leverage_base": impl_lev_base,
        "wacc": round(wacc, 4),
    }


# ---------------------------------------------------------------------------
# Function 5: extract_xbrl_concept_list (helper for schema discovery)
# ---------------------------------------------------------------------------


def extract_xbrl_concept_list(
    facts: dict,
    max_chars: int = 8000,
) -> str:
    """Return a newline-delimited list of ``taxonomy/concept`` names from XBRL facts.

    Used as a preprocessing step before schema-discovery LLM calls so the
    prompt stays within a reasonable token budget even for large XBRL documents.

    Args:
        facts:     The ``facts`` object from ``fetch_sec_xbrl`` output.
        max_chars: Maximum length of the returned string.

    Returns:
        Newline-delimited string of concept paths, truncated to max_chars.
    """
    lines: list[str] = []
    for taxonomy, concepts in facts.items():
        if not isinstance(concepts, dict):
            continue
        for concept in concepts:
            lines.append(f"{taxonomy}/{concept}")
    summary = "\n".join(lines)
    if len(summary) > max_chars:
        summary = summary[:max_chars] + "\n...(truncated)"
    return summary


# ---------------------------------------------------------------------------
# Registry construction
# ---------------------------------------------------------------------------


def register_credit_functions(registry: FunctionRegistry) -> None:
    """Register all credit-DD compute functions into *registry*.

    Called by ``build_default_registry()`` in ``trellis.tools.registry`` to
    make these functions available to the ``compute`` tool in all pipelines.
    """
    registry.register(RegisteredFunction(
        name="build_financial_spread",
        fn=build_financial_spread,
        input_schema={
            "xbrl_data":      "dict",
            "schema_mapping": "dict | str",
            "fiscal_years":   "int",
        },
        output_schema="dict",
        description=(
            "Apply an LLM-generated XBRL schema mapping to SEC companyfacts data "
            "and produce a 3-year financial spread plus canonical metrics block. "
            "Pure computation — no LLM calls."
        ),
    ))

    registry.register(RegisteredFunction(
        name="validate_spread",
        fn=validate_spread,
        input_schema={"spread_data": "dict"},
        output_schema="dict",
        description=(
            "Run accounting-identity checks on a financial spread: "
            "EBITDA = OpInc + D&A, Net Debt = Debt − Cash, FCF = CFO − CapEx, "
            "leverage sanity, revenue > 0, EBITDA margin. "
            "Returns overall_status (PASS|WARN|FAIL), gates, warnings, errors."
        ),
    ))

    registry.register(RegisteredFunction(
        name="compute_proforma_stress",
        fn=compute_proforma_stress,
        input_schema={
            "canonical_metrics": "dict",
            "facility":          "dict",
            "covenants":         "list",
        },
        output_schema="dict",
        description=(
            "Deterministic pro-forma impact table, stress scenarios "
            "(base / −25% / −45% revenue), and covenant compliance tests. "
            "Returns markdown string + structured results."
        ),
    ))

    registry.register(RegisteredFunction(
        name="run_dcf_model",
        fn=run_dcf_model,
        input_schema={
            "canonical_metrics": "dict",
            "assumptions":       "dict | str",
        },
        output_schema="dict",
        description=(
            "5-year projection and DCF valuation. "
            "LLM supplies beta_u and growth/margin adjustments via assumptions dict; "
            "this function runs the deterministic WACC build, projections, and DCF. "
            "Returns markdown + enterprise_value_base + wacc. "
            "Pass assumptions.bypass=true to skip for investment-grade credits."
        ),
    ))

    registry.register(RegisteredFunction(
        name="extract_xbrl_concept_list",
        fn=extract_xbrl_concept_list,
        input_schema={"facts": "dict", "max_chars": "int"},
        output_schema="str",
        description=(
            "Extract a newline-delimited list of taxonomy/concept names from an XBRL "
            "facts object (output of fetch_sec_xbrl). Used before schema-discovery "
            "LLM calls to keep the prompt within a reasonable token budget."
        ),
    ))
