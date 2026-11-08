.PHONY: install data api pipeline queries test all clean

install:
	pip install -r requirements-dev.txt

data:            ## generate synthetic raw exports + API source
	python data_gen/generate.py

api:             ## run the mock badge-scan API on :8000
	uvicorn api.mock_scan_api:app --port 8000

pipeline:        ## run ETL into the local DuckDB warehouse (API must be running)
	python -m pipeline.main --target duckdb

queries:         ## run all SQL and write reports/query_results.md
	python scripts/run_queries.py --target duckdb

test:
	pytest -q

all:             ## everything, end to end, locally
	bash scripts/run_local.sh

clean:
	rm -rf data/warehouse.duckdb
