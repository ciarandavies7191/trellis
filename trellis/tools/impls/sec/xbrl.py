"""SEC EDGAR XBRL and company-profile tools.

fetch_sec_xbrl
    Resolves a ticker to SEC CIK and fetches the full XBRL companyfacts JSON.
    Primary data source for spread construction and peer metric collection.

fetch_sec_company_profile
    Fetches company metadata from the EDGAR submissions endpoint: SIC code,
    fiscal year end, headquarters, state of incorporation, etc.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from trellis.config import SEC_USER_AGENT, get_http_client
from trellis.tools.base import BaseTool, ToolInput, ToolOutput
from trellis.tools.impls.fetch import resolve_ticker_to_cik

logger = logging.getLogger(__name__)

_COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
_HEADERS = {"User-Agent": SEC_USER_AGENT, "Accept": "application/json"}

# SIC descriptions for common codes; extend or replace with a full list.
_SIC_DESCRIPTIONS: dict[str, str] = {
    "5912": "Drug Stores and Proprietary Stores",
    "5411": "Grocery Stores",
    "6311": "Life Insurance",
    "6321": "Accident and Health Insurance",
    "6331": "Fire, Marine & Casualty Insurance",
    "7372": "Prepackaged Software",
    "7371": "Computer Programming Services",
    "3674": "Semiconductors",
    "3577": "Computer Peripheral Equipment",
    "5065": "Electronic Parts and Equipment",
    "2836": "Pharmaceutical Preparations",
    "2835": "In Vitro & In Vivo Diagnostic Substances",
    "8099": "Health Services, NEC",
    "6141": "Personal Credit Institutions",
    "6022": "State commercial banks",
    "6020": "National commercial banks",
}


def _concept_summary(facts: dict[str, Any], max_chars: int = 8000) -> str:
    """Return a compact listing of taxonomy/concept names for LLM schema discovery."""
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


class FetchSecXbrlTool(BaseTool):
    """Fetch the full XBRL companyfacts JSON for a ticker from SEC EDGAR.

    Returns the raw facts object plus a ``concept_list`` summary string
    suitable for passing to an LLM schema-discovery step.

    Registration name: ``fetch_sec_xbrl``
    """

    def __init__(self, name: str = "fetch_sec_xbrl") -> None:
        super().__init__(
            name,
            "Fetch SEC EDGAR XBRL companyfacts for a ticker (full facts + concept list)",
        )

    def execute(self, ticker: str, concept_list_max_chars: int = 8000, **_: Any) -> Dict[str, Any]:
        """
        Args:
            ticker: Exchange ticker symbol (case-insensitive).
            concept_list_max_chars: Max length of the ``concept_list`` summary string.

        Returns:
            dict with keys: cik, entity_name, ticker, facts, concept_list.
        """
        cik = resolve_ticker_to_cik(ticker)
        url = _COMPANYFACTS_URL.format(cik=cik)
        logger.debug("fetch_sec_xbrl: fetching %s", url)

        with get_http_client(timeout=60) as client:
            resp = client.get(url, headers=_HEADERS)
            resp.raise_for_status()
            data = resp.json()

        facts: dict[str, Any] = data.get("facts", {})
        return {
            "cik": cik,
            "entity_name": data.get("entityName", ""),
            "ticker": ticker.strip().upper(),
            "facts": facts,
            # Compact concept list for schema-discovery LLM prompt
            "concept_list": _concept_summary(facts, max_chars=concept_list_max_chars),
        }

    def get_inputs(self) -> Dict[str, ToolInput]:
        return {
            "ticker": ToolInput(
                name="ticker",
                description="Exchange ticker symbol, e.g. 'CVS'",
                required=True,
                accepted_types=(str,),
            ),
            "concept_list_max_chars": ToolInput(
                name="concept_list_max_chars",
                description="Max characters in the concept_list summary (for schema discovery)",
                required=False,
                default=8000,
            ),
        }

    def get_output(self) -> ToolOutput:
        return ToolOutput(
            name="xbrl_data",
            description="cik, entity_name, ticker, facts (full XBRL), concept_list (summary)",
            type_="object",
        )


class FetchSecCompanyProfileTool(BaseTool):
    """Fetch company metadata from the SEC EDGAR submissions endpoint.

    Returns CIK, SIC code/description, fiscal year end, headquarters,
    state of incorporation, and filing category.

    Registration name: ``fetch_sec_company_profile``
    """

    def __init__(self, name: str = "fetch_sec_company_profile") -> None:
        super().__init__(
            name,
            "Fetch company metadata from SEC EDGAR submissions (SIC, FYE, HQ, incorporation)",
        )

    def execute(self, ticker: str, **_: Any) -> Dict[str, Any]:
        """
        Args:
            ticker: Exchange ticker symbol (case-insensitive).

        Returns:
            dict with keys: cik, name, sic, sic_description, fiscal_year_end_month,
            state_of_incorporation, headquarters, category, ein.
        """
        cik = resolve_ticker_to_cik(ticker)
        url = _SUBMISSIONS_URL.format(cik=cik)
        logger.debug("fetch_sec_company_profile: fetching %s", url)

        with get_http_client(timeout=30) as client:
            resp = client.get(url, headers=_HEADERS)
            resp.raise_for_status()
            d = resp.json()

        addr = d.get("addresses", {}).get("business", {})
        city = addr.get("city", "")
        state_loc = addr.get("stateOrCountry", "")
        headquarters = f"{city}, {state_loc}".strip(", ") if city else state_loc

        sic = str(d.get("sic", ""))
        sic_desc = d.get("sicDescription") or _SIC_DESCRIPTIONS.get(sic, "")

        fye_raw: str = d.get("fiscalYearEnd", "") or ""
        fiscal_year_end_month = fye_raw[:2] if len(fye_raw) >= 2 else ""

        return {
            "cik": cik,
            "name": d.get("name", ""),
            "sic": sic,
            "sic_description": sic_desc,
            "fiscal_year_end_month": fiscal_year_end_month,
            "state_of_incorporation": d.get("stateOfIncorporation", ""),
            "headquarters": headquarters,
            "category": d.get("category", ""),
            "ein": d.get("ein", ""),
        }

    def get_inputs(self) -> Dict[str, ToolInput]:
        return {
            "ticker": ToolInput(
                name="ticker",
                description="Exchange ticker symbol",
                required=True,
                accepted_types=(str,),
            ),
        }

    def get_output(self) -> ToolOutput:
        return ToolOutput(
            name="company_profile",
            description="Company metadata: cik, name, sic, sic_description, fiscal_year_end_month, "
                        "state_of_incorporation, headquarters, category, ein",
            type_="object",
        )
