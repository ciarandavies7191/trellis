"""fetch_10k_sections — extract named sections from the most recent 10-K filing.

Fetches only the relevant text slices (risk factors, debt note, MD&A non-GAAP,
business overview) rather than the full 200-page document, saving token budget
for downstream LLM calls.

Registration name: ``fetch_10k_sections``
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from trellis.config import SEC_USER_AGENT, get_http_client
from trellis.tools.base import BaseTool, ToolInput, ToolOutput
from trellis.tools.impls.fetch import resolve_ticker_to_cik

logger = logging.getLogger(__name__)

_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
_HEADERS = {"User-Agent": SEC_USER_AGENT, "Accept": "*/*"}

# ---------------------------------------------------------------------------
# Section anchor patterns (ordered by specificity — most specific first)
# ---------------------------------------------------------------------------

_SECTION_ANCHORS: dict[str, list[str]] = {
    "risk_factors": [
        r"ITEM\s+1A[.\s]*RISK\s+FACTORS",
        r"Risk\s+Factors",
    ],
    "debt_note": [
        r"(?:NOTE|Note)\s+\d+[.\s—–-]+(?:LONG[- ]TERM\s+DEBT|BORROWINGS|DEBT\s+AND\s+CREDIT)",
        r"LONG[- ]TERM\s+DEBT\s+AND\s+CREDIT\s+FACILITIES",
        r"Long[- ]Term\s+Debt",
    ],
    "mda_nongaap": [
        r"NON-GAAP\s+(?:FINANCIAL\s+)?MEASURES",
        r"Reconciliation\s+of\s+Non-GAAP",
        r"Adjusted\s+EBITDA\s+Reconciliation",
    ],
    "business": [
        r"ITEM\s+1[.\s]*BUSINESS",
        r"Business\s+Overview",
    ],
}

_SECTION_END_ANCHORS: dict[str, str] = {
    "risk_factors": r"ITEM\s+1B|ITEM\s+2",
    "debt_note":    r"(?:NOTE|Note)\s+\d+[.\s—–-]+(?!.*DEBT)",
    "mda_nongaap":  r"ITEM\s+[89]|QUANTITATIVE\s+AND\s+QUALITATIVE",
    "business":     r"ITEM\s+1A",
}

_ALL_SECTIONS = list(_SECTION_ANCHORS.keys())


def _find_section(
    text: str,
    anchors: list[str],
    end_anchor: str,
    max_chars: int,
) -> Optional[str]:
    for anchor in anchors:
        m = re.search(anchor, text, re.IGNORECASE)
        if not m:
            continue
        start = m.start()
        end_m = re.search(end_anchor, text[start + 100:], re.IGNORECASE)
        end = start + 100 + end_m.start() if end_m else start + max_chars
        return text[start : min(end, start + max_chars)]
    return None


class Fetch10kSectionsTool(BaseTool):
    """Fetch named sections from the most recent 10-K filing.

    Extracts only the requested text slices (``risk_factors``, ``debt_note``,
    ``mda_nongaap``, ``business``) to minimise token usage in downstream steps.

    Registration name: ``fetch_10k_sections``
    """

    def __init__(self, name: str = "fetch_10k_sections") -> None:
        super().__init__(
            name,
            "Fetch named 10-K sections (risk_factors, debt_note, mda_nongaap, business)",
        )

    def execute(
        self,
        ticker: str,
        sections: Optional[List[str]] = None,
        max_chars_per_section: int = 25000,
        **_: Any,
    ) -> Dict[str, Any]:
        """
        Args:
            ticker: Exchange ticker symbol.
            sections: Which sections to extract.  Subset of
                      [risk_factors, debt_note, mda_nongaap, business].
                      Defaults to all four.
            max_chars_per_section: Character limit per extracted section.

        Returns:
            dict with keys: filing_url, filing_date, accession, and one key
            per section (value is extracted text or ``None`` if not found).
        """
        sections = sections or _ALL_SECTIONS
        cik = resolve_ticker_to_cik(ticker)

        with get_http_client(timeout=60) as client:
            # Locate most recent 10-K
            resp = client.get(
                _SUBMISSIONS_URL.format(cik=cik), headers=_HEADERS
            )
            resp.raise_for_status()
            filings = resp.json().get("filings", {}).get("recent", {})

            ten_k_idx: Optional[int] = next(
                (i for i, f in enumerate(filings.get("form", [])) if f == "10-K"),
                None,
            )
            if ten_k_idx is None:
                raise ValueError(f"No 10-K filing found for ticker {ticker!r}")

            accn = filings["accessionNumber"][ten_k_idx]
            filing_date = filings["filingDate"][ten_k_idx]
            primary_doc = filings["primaryDocument"][ten_k_idx]
            accn_path = accn.replace("-", "")
            cik_bare = str(int(cik))

            filing_url = (
                f"https://www.sec.gov/Archives/edgar/data/"
                f"{cik_bare}/{accn_path}/{primary_doc}"
            )
            logger.debug("fetch_10k_sections: fetching %s", filing_url)

            resp2 = client.get(filing_url, headers=_HEADERS, timeout=60)
            resp2.raise_for_status()
            text = resp2.text

        result: Dict[str, Any] = {
            "filing_url": filing_url,
            "filing_date": filing_date,
            "accession": accn,
        }
        for section in sections:
            anchors = _SECTION_ANCHORS.get(section, [])
            end_anchor = _SECTION_END_ANCHORS.get(section, r"ITEM\s+\d+")
            result[section] = _find_section(
                text, anchors, end_anchor, max_chars_per_section
            )

        # Ensure all four section keys are present (None if not requested)
        for s in _ALL_SECTIONS:
            result.setdefault(s, None)

        return result

    def get_inputs(self) -> Dict[str, ToolInput]:
        return {
            "ticker": ToolInput(
                name="ticker",
                description="Exchange ticker symbol",
                required=True,
                accepted_types=(str,),
            ),
            "sections": ToolInput(
                name="sections",
                description=(
                    "Which sections to extract: risk_factors, debt_note, "
                    "mda_nongaap, business.  Defaults to all four."
                ),
                required=False,
                default=None,
            ),
            "max_chars_per_section": ToolInput(
                name="max_chars_per_section",
                description="Character limit per section (default 25000)",
                required=False,
                default=25000,
            ),
        }

    def get_output(self) -> ToolOutput:
        return ToolOutput(
            name="sections",
            description="filing_url, filing_date, accession, risk_factors, "
                        "debt_note, mda_nongaap, business (each string or null)",
            type_="object",
        )
