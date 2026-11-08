import importlib
import json

import pytest
from fastapi.testclient import TestClient

from pipeline.extract import ApiError, ScanApiClient


@pytest.fixture
def api(tmp_path, monkeypatch):
    path = tmp_path / "scans.jsonl"
    path.write_text("\n".join(json.dumps({"scan_id": f"S{i}", "event_code": "E1" if i % 2 else "E2"}) for i in range(23)))
    monkeypatch.setenv("SCAN_DATA_PATH", str(path))
    monkeypatch.setenv("SCAN_API_KEY", "k")
    monkeypatch.setenv("MOCK_API_FAILURE_RATE", "0")
    import api.mock_scan_api as mod
    mod = importlib.reload(mod)
    with TestClient(mod.app) as client:  # context manager runs the startup data load
        yield client


def test_requires_api_key(api):
    assert api.get("/v1/scans").status_code == 401


def test_client_paginates_through_everything(api):
    client = ScanApiClient("http://testserver", "k", page_size=5, session=api)
    df = client.fetch_all()
    assert len(df) == 23 and df["scan_id"].is_unique
    assert client.stats["requests"] == 5


def test_client_filters_by_event(api):
    df = ScanApiClient("http://testserver", "k", page_size=4, session=api).fetch_all(event_code="E1")
    assert set(df["event_code"]) == {"E1"} and len(df) == 11


class FlakySession:
    """Fails twice with 503, then delegates to the real app."""

    def __init__(self, inner, failures=2, status=503):
        self.inner, self.failures, self.status, self.headers = inner, failures, status, {}

    def get(self, url, **kw):
        if self.failures:
            self.failures -= 1
            return type("R", (), {"status_code": self.status, "headers": {}, "text": "down"})()
        return self.inner.get(url, headers=self.headers, **kw)


def test_client_retries_transient_errors(api):
    client = ScanApiClient("http://testserver", "k", page_size=50, backoff=0.01, session=FlakySession(api))
    assert len(client.fetch_all()) == 23
    assert client.stats["retries"] == 2


def test_client_does_not_retry_client_errors(api):
    client = ScanApiClient("http://testserver", "k", backoff=0.01, session=FlakySession(api, failures=1, status=400))
    with pytest.raises(ApiError):
        client.fetch_all()
