#!/usr/bin/env bash
# End-to-end local run: generate data, start the mock API, run the pipeline, run the SQL, stop the API.
set -euo pipefail
cd "$(dirname "$0")/.."

[ -f data/api_source/scans.jsonl ] || python data_gen/generate.py

uvicorn api.mock_scan_api:app --port 8000 --log-level warning &
API_PID=$!
trap 'kill $API_PID 2>/dev/null || true' EXIT
for _ in $(seq 1 30); do curl -sf http://127.0.0.1:8000/health >/dev/null && break; sleep 0.5; done

python -m pipeline.main --target duckdb
python scripts/run_queries.py --target duckdb
