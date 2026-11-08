"""
Mock badge-scanning vendor API.

Exhibitions use a third-party vendor for entry-gate and booth lead scanning; the data is only
reachable through their REST API. This FastAPI app imitates one, including the annoying parts:

  * API-key auth via the X-API-Key header
  * cursor-based pagination (opaque cursor, page_size capped at 5000)
  * random 503s and 429s with Retry-After (MOCK_API_FAILURE_RATE, default 5%)

Run locally:   uvicorn api.mock_scan_api:app --port 8000

The data file is loaded once at startup (before the server accepts traffic), and kept as raw
JSON lines rather than parsed dicts, which keeps memory low on a small Cloud Run instance.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import random
import re
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import JSONResponse, Response

ROOT = Path(__file__).resolve().parents[1]
MAX_PAGE = 5000
EVENT_RE = re.compile(r'"event_code":\s*"([^"]+)"')

log = logging.getLogger("uvicorn.error")


class ScanStore:
    def __init__(self):
        self.lines: list[str] = []
        self.by_event: dict[str, list[int]] = {}

    def load(self, path: str) -> None:
        t0 = time.perf_counter()
        if path.startswith("gs://"):  # on Cloud Run the source file lives in GCS
            from google.cloud import storage

            bucket, _, blob = path[5:].partition("/")
            local = Path(tempfile.gettempdir()) / "scans.jsonl"
            storage.Client().bucket(bucket).blob(blob).download_to_filename(local)
            path = str(local)
        with open(path) as f:
            self.lines = [line.rstrip("\n") for line in f if line.strip()]
        self.by_event = {}
        for i, line in enumerate(self.lines):
            m = EVENT_RE.search(line)
            if m:
                self.by_event.setdefault(m.group(1), []).append(i)
        log.info("loaded %d scan records in %.1fs", len(self.lines), time.perf_counter() - t0)


store = ScanStore()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    store.load(os.getenv("SCAN_DATA_PATH", str(ROOT / "data" / "api_source" / "scans.jsonl")))
    yield


app = FastAPI(title="Mock Badge Scan API", version="1.1", lifespan=lifespan)


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
    return {"status": "ok", "records": len(store.lines)}


@app.get("/v1/scans")
def list_scans(
    cursor: str | None = None,
    page_size: int = Query(1000, ge=1, le=MAX_PAGE),
    event_code: str | None = None,
    x_api_key: str | None = Header(default=None),
):
    if x_api_key != os.getenv("SCAN_API_KEY", "local-dev-key"):
        raise HTTPException(status_code=401, detail="missing or invalid API key")

    failure_rate = float(os.getenv("MOCK_API_FAILURE_RATE", "0.05"))
    r = random.random()
    if r < failure_rate * 0.2:
        return JSONResponse(status_code=429, content={"detail": "rate limited"}, headers={"Retry-After": "1"})
    if r < failure_rate:
        return JSONResponse(status_code=503, content={"detail": "upstream temporarily unavailable"})

    idx = store.by_event.get(event_code, []) if event_code else None
    total = len(idx) if idx is not None else len(store.lines)
    start = decode_cursor(cursor)
    end = min(start + page_size, total)
    rows = [store.lines[i] for i in idx[start:end]] if idx is not None else store.lines[start:end]
    next_cursor = json.dumps(encode_cursor(end)) if end < total else "null"
    # the records are already JSON, so splice them in rather than re-serialising
    body = f'{{"data":[{",".join(rows)}],"count":{len(rows)},"total":{total},"next_cursor":{next_cursor}}}'
    return Response(content=body, media_type="application/json")
