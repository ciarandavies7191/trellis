import urllib.request

import pytest

from trellis.tools.impls.search import SearchWebTool


class StubHTTPResponse:
    def __init__(self, body: bytes):
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


@pytest.mark.parametrize("provider", [None, "duckduckgo", "serpapi"])  # serpapi falls back without key
def test_search_web_parses_duckduckgo_html(monkeypatch, provider):
    # Minimal DuckDuckGo HTML with two results; tool will cap by top_n
    sample_html = (
        '<div class="result">'
        '<a class="result__a" href="/l/?kh=-1&uddg=https%3A%2F%2Fti.com%2Fpresentations%2F2023">TI 2023 Presentation</a>'
        '<div class="result__snippet">Deck PDF and summary</div>'
        '</div>'
        '<div class="result">'
        '<a class="result__a" href="https://example.com/ti/2024">TI 2024 Presentation</a>'
        '<div class="result__snippet">Slides and webcast</div>'
        '</div>'
    ).encode("utf-8")

    def fake_urlopen(req, timeout=15):  # noqa: D401
        return StubHTTPResponse(sample_html)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    tool = SearchWebTool()
    out = tool.execute(
        query=[
            "Texas Instruments investor day presentation 2023 site:ti.com OR filetype:pdf",
            "Texas Instruments investor day presentation 2024 site:ti.com OR filetype:pdf",
        ],
        top_n=1,
        provider=provider if provider is not None else None,
        timeout=5,
    )

    assert out["status"] == "success"
    results = out["results"]
    # We requested 2 queries with top_n=1 each => 2 results total
    assert len(results) == 2
    for r in results:
        assert set(["title", "snippet", "url", "source_query"]).issubset(r.keys())
        assert r["title"]
        assert r["url"].startswith("https://") or r["url"].startswith("http://")


def test_search_web_accepts_string_query(monkeypatch):
    sample_html = (
        '<a class="result__a" href="/l/?uddg=https%3A%2F%2Fti.com%2Fidp%2F2023">TI IDP 2023</a>'
        '<div class="result__snippet">Investor day deck</div>'
    ).encode("utf-8")

    def fake_urlopen(req, timeout=15):  # noqa: D401
        return StubHTTPResponse(sample_html)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    tool = SearchWebTool()
    out = tool.execute(query="ti investor day 2023", top_n=1, timeout=5)

    assert out["status"] == "success"
    results = out["results"]
    assert len(results) == 1
    r0 = results[0]
    assert r0["title"].lower().startswith("ti")
    assert r0["url"].startswith("https://ti.com/")

