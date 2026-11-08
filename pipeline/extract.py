"""Extract step: raw CSV exports (local folder or GCS landing zone) + the badge-scan REST API."""
from __future__ import annotations

import logging
import random
import tempfile
import time
from pathlib import Path

import pandas as pd
import requests

log = logging.getLogger(__name__)

RAW_FILES = ["cities", "events", "exhibitors", "exhibitor_participation", "visitors", "registrations"]


def _download_from_gcs(uri: str) -> Path:
    """Copy gs://bucket/prefix/*.csv into a temp dir and return it."""
    from google.cloud import storage

    bucket_name, _, prefix = uri[5:].partition("/")
    dest = Path(tempfile.mkdtemp(prefix="raw_"))
    client = storage.Client()
    for name in RAW_FILES:
        blob = client.bucket(bucket_name).blob(f"{prefix.rstrip('/')}/{name}.csv".lstrip("/"))
        blob.download_to_filename(dest / f"{name}.csv")
        log.info("downloaded gs://%s/%s", bucket_name, blob.name)
    return dest


def read_raw_files(source: str) -> dict[str, pd.DataFrame]:
    """Read every raw export as strings; typing happens in the transform step."""
    folder = _download_from_gcs(source) if source.startswith("gs://") else Path(source)
    frames = {}
    for name in RAW_FILES:
        path = folder / f"{name}.csv"
        if not path.exists():
            raise FileNotFoundError(f"missing raw export: {path}")
        frames[name] = pd.read_csv(path, dtype=str, keep_default_na=False)
        log.info("read %-24s %8d rows", name, len(frames[name]))
    return frames


class ApiError(RuntimeError):
    pass


class ScanApiClient:
    """Client for the badge-scan vendor API with cursor pagination and retry/backoff."""

    RETRYABLE = {429, 500, 502, 503, 504}

    def __init__(self, base_url: str, api_key: str, page_size: int = 5000,
                 max_retries: int = 6, backoff: float = 0.5, timeout: float = 30.0,
                 session: requests.Session | None = None):
        self.base_url = base_url.rstrip("/")
        self.page_size = page_size
        self.max_retries = max_retries
        self.backoff = backoff
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.headers.update({"X-API-Key": api_key, "User-Agent": "event-analytics-pipeline/1.0"})
        self.stats = {"requests": 0, "retries": 0}

    def _get(self, path: str, params: dict) -> dict:
        for attempt in range(self.max_retries + 1):
            self.stats["requests"] += 1
            try:
                resp = self.session.get(f"{self.base_url}{path}", params=params, timeout=self.timeout)
            except requests.ConnectionError as exc:
                if attempt == self.max_retries:
                    raise ApiError(f"cannot reach scan API at {self.base_url}: {exc}") from exc
                status, wait = None, self.backoff * 2 ** attempt
            else:
                if resp.status_code == 200:
                    return resp.json()
                status = resp.status_code
                if status not in self.RETRYABLE or attempt == self.max_retries:
                    raise ApiError(f"GET {path} failed with {status}: {resp.text[:200]}")
                retry_after = resp.headers.get("Retry-After")
                wait = float(retry_after) if retry_after else self.backoff * 2 ** attempt
            wait += random.uniform(0, self.backoff)  # jitter
            self.stats["retries"] += 1
            log.warning("scan API %s, retry %d in %.1fs", status or "connection error", attempt + 1, wait)
            time.sleep(wait)
        raise ApiError("unreachable")  # pragma: no cover

    def iter_pages(self, event_code: str | None = None):
        cursor = None
        while True:
            params = {"page_size": self.page_size}
            if cursor:
                params["cursor"] = cursor
            if event_code:
                params["event_code"] = event_code
            body = self._get("/v1/scans", params)
            yield body["data"]
            cursor = body.get("next_cursor")
            if not cursor:
                break

    def fetch_all(self, event_code: str | None = None) -> pd.DataFrame:
        records: list[dict] = []
        for i, page in enumerate(self.iter_pages(event_code), start=1):
            records.extend(page)
            if i % 20 == 0:
                log.info("  fetched %d scan records so far", len(records))
        log.info("fetched %d scan records in %d requests (%d retries)",
                 len(records), self.stats["requests"], self.stats["retries"])
        return pd.DataFrame.from_records(records)
