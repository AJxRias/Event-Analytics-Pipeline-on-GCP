"""Load step: BigQuery (production) or DuckDB (local development). Same table names in both."""
from __future__ import annotations

import logging
import tempfile
from pathlib import Path

import pandas as pd

import config

log = logging.getLogger(__name__)

# BigQuery physical design: partition big facts by date and cluster by the columns
# analysts filter on most, so typical per-event queries scan a fraction of the table.
BQ_TABLE_DESIGN = {
    "fact_scans": {"partition": "scan_date", "cluster": ["event_id", "scan_type", "exhibitor_id"]},
    "fact_registrations": {"partition": "registration_date", "cluster": ["event_id", "channel"]},
    "fact_participation": {"cluster": ["event_id"]},
}
APPEND_TABLES = {"pipeline_runs", "dq_results"}


class DuckDBLoader:
    def __init__(self, path: Path = config.DUCKDB_PATH, schema: str = config.DATASET):
        import duckdb

        path.parent.mkdir(parents=True, exist_ok=True)
        self.con = duckdb.connect(str(path))
        self.schema = schema
        self.con.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
        self.where = f"DuckDB file {path}"

    def write(self, name: str, df: pd.DataFrame, run_id: str) -> None:
        self.con.register("_df", df)
        if name in APPEND_TABLES:
            self.con.execute(f"CREATE TABLE IF NOT EXISTS {self.schema}.{name} AS SELECT * FROM _df WHERE false")
            self.con.execute(f"INSERT INTO {self.schema}.{name} BY NAME SELECT * FROM _df")
        else:
            self.con.execute(f"CREATE OR REPLACE TABLE {self.schema}.{name} AS SELECT * FROM _df")
        self.con.unregister("_df")
        log.info("  loaded %-20s %8d rows", name, len(df))

    def close(self):
        self.con.close()


class BigQueryLoader:
    def __init__(self):
        from google.cloud import bigquery

        self.bq = bigquery
        self.client = bigquery.Client(project=config.GCP_PROJECT, location=config.BQ_LOCATION)
        self.dataset = f"{self.client.project}.{config.BQ_DATASET}"
        ds = bigquery.Dataset(self.dataset)
        ds.location = config.BQ_LOCATION
        self.client.create_dataset(ds, exists_ok=True)
        self.bucket = config.GCS_BUCKET
        self.where = f"BigQuery dataset {self.dataset}"

    def _job_config(self, name: str):
        bq = self.bq
        design = BQ_TABLE_DESIGN.get(name, {})
        cfg = bq.LoadJobConfig(
            write_disposition=bq.WriteDisposition.WRITE_APPEND if name in APPEND_TABLES
            else bq.WriteDisposition.WRITE_TRUNCATE,
        )
        if "partition" in design:
            cfg.time_partitioning = bq.TimePartitioning(type_=bq.TimePartitioningType.DAY, field=design["partition"])
        if "cluster" in design:
            cfg.clustering_fields = design["cluster"]
        return cfg

    def write(self, name: str, df: pd.DataFrame, run_id: str) -> None:
        table_id = f"{self.dataset}.{name}"
        cfg = self._job_config(name)
        if self.bucket:
            # stage as parquet in GCS (kept per run for lineage / replay), then load from there
            from google.cloud import storage

            cfg.source_format = self.bq.SourceFormat.PARQUET
            cfg.parquet_options = self.bq.ParquetOptions()
            cfg.parquet_options.enable_list_inference = True
            local = Path(tempfile.mkdtemp()) / f"{name}.parquet"
            df.to_parquet(local, index=False, coerce_timestamps="us", allow_truncated_timestamps=True)
            blob_name = f"curated/run={run_id}/{name}.parquet"
            storage.Client(project=self.client.project).bucket(self.bucket).blob(blob_name).upload_from_filename(local)
            job = self.client.load_table_from_uri(f"gs://{self.bucket}/{blob_name}", table_id, job_config=cfg)
        else:
            job = self.client.load_table_from_dataframe(df, table_id, job_config=cfg)
        job.result()
        log.info("  loaded %-20s %8d rows -> %s", name, len(df), table_id)

    def close(self):
        self.client.close()


def get_loader(target: str):
    if target == "bigquery":
        return BigQueryLoader()
    if target == "duckdb":
        return DuckDBLoader()
    raise ValueError(f"unknown target {target!r} (use 'duckdb' or 'bigquery')")
