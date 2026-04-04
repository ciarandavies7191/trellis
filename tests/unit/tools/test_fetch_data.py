import json
import types
import urllib.request

import pytest

from trellis.tools.impls.fetch import FetchTool, _build_filing_url


class StubHeaders:
    def __init__(self, mapping: dict[str, str]):
        self._m = mapping

    def get(self, k: str, d: str | None = None):  # noqa: D401
        return self._m.get(k, d)


class StubHTTPResponse:
    def __init__(self, body: bytes, headers: dict[str, str] | None = None):
        self._body = body
        self.headers = StubHeaders(headers or {})

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


@pytest.fixture(autouse=True)
def stub_user_agent(monkeypatch):
    monkeypatch.setenv("SEC_USER_AGENT", "TrellisTest/1.0 (test@example.com)")


def test_fetch_url_json(monkeypatch):
    tool = FetchTool()

    body = json.dumps({"ok": True}).encode("utf-8")

    def fake_urlopen(req, timeout=20):  # noqa: D401
        return StubHTTPResponse(body, headers={"Content-Type": "application/json"})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    out = tool.execute(source="url", url="https://example.com/api")
    assert out["status"] == "success"
    assert out["source"] == "url"
    assert out["content_type"].startswith("application/json")
    assert out["data"]["ok"] is True


def test_build_filing_url():
    url = _build_filing_url("0000320193", "0000320193-23-000077", "a10k.htm")
    assert "/Archives/edgar/data/320193/000032019323000077/a10k.htm" in url
