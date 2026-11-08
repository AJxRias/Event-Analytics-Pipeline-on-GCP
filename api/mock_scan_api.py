"""
Mock badge-scanning vendor API.

Exhibitions use a third-party vendor for entry-gate and booth lead scanning; the data is only
reachable through their REST API. This FastAPI app imitates one, including the annoying parts:

  * API-key auth via the X-API-Key header
  * cursor-based pagination (opaque cursor, page_size capped at 5000)
  * random 503s and 429s with Retry-After (MOCK_API_FAILURE_RATE, default 5%)

Run locally:   uvicorn api.mock_scan_api:app --port 8000
"""
from __future__ import annotations

import base64
import json
import os
import random
import tempfile
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import JSONResponse

ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = os.getenv("SCAN_DATA_PATH", str(ROOT / "data" / "api_source" / "scans.jsonl"))
API_KEY = os.getenv("SCAN_API_KEY", "local-dev-key")
FAILURE_RATE = float(os.getenv("MOCK_API_FAILURE_RATE", "0.05"))
MAX_PAGE = 5000

app = FastAPI(title="Mock Badge Scan API", version="1.0")


@lru_cache(maxsize=1)
def load_scans() -> list[dict]:
    path = DATA_PATH
    if path.startswith("gs://"):  # when deployed on Cloud Run the source file lives in GCS
        from google.cloud import storage

        bucket, _, blob = path[5:].partition("/")
        local = Path(tempfile.gettempdir()) / "scans.jsonl"
        storage.Client().bucket(bucket).blob(blob).download_to_filename(local)
        path = str(local)
    with open(path) as f:
        return [json.loads(line) for line in f]


def encode_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(f"o:{offset}".encode()).decode()


def decode_cursor(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        return int(base64.urlsafe_b64decode(cursor.encode()).decode().split(":", 1)[1])
    except Exception:
        raise HTTPException(status_code=400, detail="invalid cursor")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/v1/scans")
def list_scans(
    cursor: str | None = None,
    page_size: int = Query(1000, ge=1, le=MAX_PAGE),
    event_code: str | None = None,
    x_api_key: str | None = Header(default=None),
):
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="missing or invalid API key")

    r = random.random()
    if r < FAILURE_RATE * 0.2:
        return JSONResponse(status_code=429, content={"detail": "rate limited"}, headers={"Retry-After": "1"})
    if r < FAILURE_RATE:
        return JSONResponse(status_code=503, content={"detail": "upstream temporarily unavailable"})

    scans = load_scans()
    if event_code:
        scans = [s for s in scans if s["event_code"] == event_code]
    start = decode_cursor(cursor)
    page = scans[start:start + page_size]
    nxt = start + len(page)
    return {
        "data": page,
        "count": len(page),
        "total": len(scans),
        "next_cursor": encode_cursor(nxt) if nxt < len(scans) else None,
    }
