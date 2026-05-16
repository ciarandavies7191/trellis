"""fetch_sec_ratings — scan recent SEC filings for credit-rating disclosures.

Scans the most recent 8-K, 10-K, and 10-Q filings within a configurable
lookback window.  Returns structured ratings per agency (S&P, Moody's, Fitch)
with provenance (form type, filing date, accession number) so downstream LLM
prompts can cite them accurately.

Registration name: ``fetch_sec_ratings``
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Any, Dict

from trellis.config import SEC_USER_AGENT, get_http_client
from trellis.tools.base import BaseTool, ToolInput, ToolOutput
from trellis.tools.impls.fetch import resolve_ticker_to_cik

logger = logging.getLogger(__name__)

_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
_FILING_URL = (
    "https://www.sec.gov/Archives/edgar/data/{cik_bare}/{accn_path}/{doc}"
)
_HEADERS = {"User-Agent": SEC_USER_AGENT, "Accept": "*/*"}

# ---------------------------------------------------------------------------
# Rating-grade regex patterns
# ---------------------------------------------------------------------------

_SP_GRADES = r"(?:AAA|AA[+-]?|A[+-]?|BBB[+-]?|BB[+-]?|B[+-]?|CCC[+-]?|CC|SD|D)"
_MOODYS_GRADES = r"(?:Aaa|Aa[123]|A[123]|Baa[123]|Ba[123]|B[123]|Caa[123]|Ca|C)"

_SP_PATTERN = re.compile(
    rf"(?:S&P|Standard\s*&\s*Poor|S\s*&\s*P)[^.{{}}]*?({_SP_GRADES})", re.IGNORECASE
)
_MOODYS_PATTERN = re.compile(
    rf"(?:Moody'?s|Moody\s+Investors)[^.{{}}]*?({_MOODYS_GRADES})", re.IGNORECASE
)
_FITCH_PATTERN = re.compile(
    rf"Fitch[^.{{}}]*?({_SP_GRADES})", re.IGNORECASE
)
_GENERIC_PATTERN = re.compile(
    rf"(?:rated?|rating|affirmed?|upgraded?|downgraded?|issuer credit)[^.{{}}]*?"
    rf"({_SP_GRADES}|{_MOODYS_GRADES})",
    re.IGNORECASE,
)


def _accn_to_path(accn: str) -> str:
    return accn.replace("-", "")


class FetchSecRatingsTool(BaseTool):
    """Scan recent SEC filings for credit-rating disclosures.

    Searches 8-K, 10-K, and 10-Q filings filed within *lookback_months*
    for pattern-matched credit ratings from S&P, Moody's, and Fitch.
    Returns structured results with filing provenance.

    Registration name: ``fetch_sec_ratings``
    """

    def __init__(self, name: str = "fetch_sec_ratings") -> None:
        super().__init__(
            name,
            "Scan recent SEC 8-K/10-K/10-Q filings for credit-rating disclosures",
        )

    def execute(
        self,
        ticker: str,
        lookback_months: int = 24,
        **_: Any,
    ) -> Dict[str, Any]:
        """
        Args:
            ticker: Exchange ticker symbol.
            lookback_months: How many months of filings to scan (default 24).

        Returns:
            dict with keys: sp, sp_source, moodys, moodys_source, fitch,
            fitch_source, generic, sources.
        """
        cik = resolve_ticker_to_cik(ticker)
        cutoff = datetime.utcnow() - timedelta(days=lookback_months * 30)

        with get_http_client(timeout=30) as client:
            resp = client.get(
                _SUBMISSIONS_URL.format(cik=cik), headers=_HEADERS
            )
            resp.raise_for_status()
            submissions = resp.json()

            filings = submissions.get("filings", {}).get("recent", {})
            forms = filings.get("form", [])
            dates = filings.get("filingDate", [])
            accns = filings.get("accessionNumber", [])
            primary_docs = filings.get("primaryDocument", [])

            results: Dict[str, Any] = {
                "sp": None, "sp_source": None,
                "moodys": None, "moodys_source": None,
                "fitch": None, "fitch_source": None,
                "generic": None, "sources": [],
            }

            cik_bare = str(int(cik))  # drop leading zeros for archive URL

            for form, date_str, accn, doc in zip(
                forms, dates, accns, primary_docs
            ):
                if form not in ("8-K", "10-K", "10-Q"):
                    continue
                try:
                    filing_date = datetime.strptime(date_str, "%Y-%m-%d")
                except ValueError:
                    continue
                if filing_date < cutoff:
                    break  # filings are newest-first; stop when outside window

                accn_path = _accn_to_path(accn)
                url = _FILING_URL.format(
                    cik_bare=cik_bare, accn_path=accn_path, doc=doc
                )
                try:
                    text_resp = client.get(url, headers=_HEADERS, timeout=30)
                    text = text_resp.text[:150_000]  # cap per filing
                except Exception as exc:
                    logger.debug(
                        "fetch_sec_ratings: skipping %s (%s)", url, exc
                    )
                    continue

                source_meta = {
                    "form": form,
                    "filing_date": date_str,
                    "accession": accn,
                }

                for label, pattern, key in [
                    ("S&P", _SP_PATTERN, "sp"),
                    ("Moody's", _MOODYS_PATTERN, "moodys"),
                    ("Fitch", _FITCH_PATTERN, "fitch"),
                ]:
                    if results[key] is None:
                        m = pattern.search(text)
                        if m:
                            results[key] = m.group(1)
                            results[f"{key}_source"] = source_meta
                            results["sources"].append(
                                {"agency": label, "rating": m.group(1), **source_meta}
                            )

                # Generic fallback — attribution-free rating mention
                if (
                    not any(results[k] for k in ["sp", "moodys", "fitch"])
                    and results["generic"] is None
                ):
                    gm = _GENERIC_PATTERN.search(text)
                    if gm:
                        results["generic"] = gm.group(1)

                if all(results[k] for k in ["sp", "moodys", "fitch"]):
                    break  # all three found

        return results

    def get_inputs(self) -> Dict[str, ToolInput]:
        return {
            "ticker": ToolInput(
                name="ticker",
                description="Exchange ticker symbol",
                required=True,
                accepted_types=(str,),
            ),
            "lookback_months": ToolInput(
                name="lookback_months",
                description="How many months of filings to scan (default 24)",
                required=False,
                default=24,
            ),
        }

    def get_output(self) -> ToolOutput:
        return ToolOutput(
            name="ratings",
            description="sp, sp_source, moodys, moodys_source, fitch, fitch_source, "
                        "generic, sources (list of provenance objects)",
            type_="object",
        )
