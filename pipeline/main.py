"""
Event analytics pipeline: extract -> transform -> validate -> load.

    python -m pipeline.main                      # local: raw CSVs + mock API -> DuckDB
    PIPELINE_TARGET=bigquery python -m pipeline.main   # GCP: GCS landing zone + API -> BigQuery

Every run is recorded in the pipeline_runs table and its data-quality results in dq_results.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config  # noqa: E402
from pipeline.extract import ScanApiClient, read_raw_files  # noqa: E402
from pipeline.load import get_loader  # noqa: E402
from pipeline.transform import Transformer  # noqa: E402
from pipeline.validate import DataQualityError, enforce, run_checks  # noqa: E402

log = logging.getLogger("pipeline")


def run(target: str, raw_source: str, api_url: str, api_key: str) -> dict:
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]
    started = datetime.now(timezone.utc)
    t0 = time.perf_counter()
    log.info("run %s | target=%s | raw=%s | api=%s", run_id, target, raw_source, api_url)

    loader = get_loader(target)
    status, error, rows, dq = "failed", None, {}, []
    try:
        log.info("[1/4] extract")
        raw = read_raw_files(raw_source)
        client = ScanApiClient(api_url, api_key, page_size=config.SCAN_API_PAGE_SIZE)
        scans = client.fetch_all()

        log.info("[2/4] transform")
        result = Transformer(raw, scans).run()

        log.info("[3/4] validate")
        dq = run_checks(result.tables, raw_scan_count=len(scans))
        dq += [{**i, "passed": False, "detail": "cleaned in transform"} for i in result.issues]
        enforce(dq)

        log.info("[4/4] load -> %s", loader.where)
        for name, df in result.tables.items():
            loader.write(name, df, run_id)
            rows[name] = len(df)
        status = "success"
    except DataQualityError as exc:
        error = str(exc)
        log.error(error)
    except Exception as exc:  # recorded in pipeline_runs, then re-raised so the job exits non-zero
        error = f"{type(exc).__name__}: {exc}"
        log.exception("pipeline failed")
    finally:
        finished = datetime.now(timezone.utc)
        run_row = pd.DataFrame([{
            "run_id": run_id, "started_at": started.replace(tzinfo=None), "finished_at": finished.replace(tzinfo=None),
            "duration_seconds": round(time.perf_counter() - t0, 2), "status": status, "target": target,
            "rows_loaded": json.dumps(rows), "dq_errors": sum(1 for d in dq if d["severity"] == "error" and not d["passed"]),
            "dq_warnings": sum(1 for d in dq if d["severity"] == "warning" and not d["passed"]), "error": error,
        }])
        try:
            loader.write("pipeline_runs", run_row, run_id)
            if dq:
                dq_df = pd.DataFrame(dq)
                dq_df.insert(0, "run_id", run_id)
                loader.write("dq_results", dq_df[["run_id", "check_name", "table_name", "severity",
                                                  "failed_rows", "passed", "detail"]], run_id)
        finally:
            loader.close()

    log.info("run %s finished: %s in %.1fs", run_id, status, time.perf_counter() - t0)
    if status != "success":
        raise SystemExit(1)
    return {"run_id": run_id, "rows": rows}


def main():
    ap = argparse.ArgumentParser(description="Event analytics ETL pipeline")
    ap.add_argument("--target", default=config.PIPELINE_TARGET, choices=["duckdb", "bigquery"])
    ap.add_argument("--raw-source", default=config.RAW_SOURCE, help="local folder or gs://bucket/prefix")
    ap.add_argument("--api-url", default=config.SCAN_API_URL)
    ap.add_argument("--api-key", default=config.SCAN_API_KEY)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
                        datefmt="%H:%M:%S")
    run(args.target, args.raw_source, args.api_url, args.api_key)


if __name__ == "__main__":
    main()
