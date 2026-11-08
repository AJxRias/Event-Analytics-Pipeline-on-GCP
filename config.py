"""Central configuration and reference data shared by the generator, pipeline and API."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# --- Runtime settings (overridable with environment variables) -------------------------
PIPELINE_TARGET = os.getenv("PIPELINE_TARGET", "duckdb")  # "duckdb" (local) or "bigquery"
RAW_SOURCE = os.getenv("RAW_SOURCE", str(ROOT / "data" / "raw"))  # local dir or gs://bucket/prefix
DUCKDB_PATH = Path(os.getenv("DUCKDB_PATH", ROOT / "data" / "warehouse.duckdb"))

GCP_PROJECT = os.getenv("GCP_PROJECT")  # falls back to the client's default project
BQ_DATASET = os.getenv("BQ_DATASET", "event_analytics")
BQ_LOCATION = os.getenv("BQ_LOCATION", "asia-south1")  # Mumbai region
GCS_BUCKET = os.getenv("GCS_BUCKET")  # staging bucket for parquet files (BigQuery target)

SCAN_API_URL = os.getenv("SCAN_API_URL", "http://127.0.0.1:8000")
SCAN_API_KEY = os.getenv("SCAN_API_KEY", "local-dev-key").strip()  # secrets often end in a newline
SCAN_API_PAGE_SIZE = int(os.getenv("SCAN_API_PAGE_SIZE", "5000"))

# Analytics dataset / schema name. The same name is used for the DuckDB schema so that
# the SQL in sql/analysis runs unchanged against both warehouses.
DATASET = BQ_DATASET

# --- Reference data ----------------------------------------------------------------------
# city, code (IATA), state, tier, region, raw spelling variants seen in source systems,
# first year the city hosted a show (tier 3 cities were added in 2025).
CITIES = [
    ("Mumbai", "BOM", "Maharashtra", 1, "West", ["Mumbai", "mumbai", "Bombay", "MUMBAI ", "Navi Mumbai"], 2024),
    ("Delhi", "DEL", "Delhi", 1, "North", ["Delhi", "New Delhi", "delhi ncr", "DELHI"], 2024),
    ("Bengaluru", "BLR", "Karnataka", 1, "South", ["Bengaluru", "Bangalore", "bangalore", "B'lore"], 2024),
    ("Chennai", "MAA", "Tamil Nadu", 1, "South", ["Chennai", "Madras", "chennai "], 2024),
    ("Hyderabad", "HYD", "Telangana", 1, "South", ["Hyderabad", "hyderabad", "Secunderabad"], 2024),
    ("Kolkata", "CCU", "West Bengal", 1, "East", ["Kolkata", "Calcutta", "kolkata"], 2024),
    ("Pune", "PNQ", "Maharashtra", 1, "West", ["Pune", "Poona", "pune"], 2024),
    ("Ahmedabad", "AMD", "Gujarat", 1, "West", ["Ahmedabad", "Amdavad", "ahmedabad"], 2024),
    ("Indore", "IDR", "Madhya Pradesh", 2, "Central", ["Indore", "indore"], 2024),
    ("Jaipur", "JAI", "Rajasthan", 2, "North", ["Jaipur", "jaipur", "JAIPUR"], 2024),
    ("Lucknow", "LKO", "Uttar Pradesh", 2, "North", ["Lucknow", "lucknow"], 2024),
    ("Coimbatore", "CJB", "Tamil Nadu", 2, "South", ["Coimbatore", "Kovai", "coimbatore"], 2024),
    ("Nagpur", "NAG", "Maharashtra", 2, "West", ["Nagpur", "nagpur"], 2024),
    ("Kochi", "COK", "Kerala", 2, "South", ["Kochi", "Cochin", "kochi"], 2024),
    ("Surat", "STV", "Gujarat", 2, "West", ["Surat", "surat"], 2024),
    ("Bhubaneswar", "BBI", "Odisha", 2, "East", ["Bhubaneswar", "Bhubaneshwar", "BBSR"], 2024),
    ("Kolhapur", "KLH", "Maharashtra", 3, "West", ["Kolhapur", "kolhapur"], 2025),
    ("Hubballi", "HBX", "Karnataka", 3, "South", ["Hubballi", "Hubli", "Hubli-Dharwad"], 2025),
    ("Udaipur", "UDR", "Rajasthan", 3, "North", ["Udaipur", "udaipur"], 2025),
]

INDUSTRIES = {
    "AI": "Architecture & Interiors",
    "CI": "Construction & Infrastructure",
    "KB": "Kitchen & Bath",
    "LE": "Lighting & Electricals",
    "SH": "Smart Homes & Automation",
    "SF": "Surfaces & Flooring",
    "DW": "Doors, Windows & Facades",
    "FF": "Furniture & Furnishings",
}

VISITOR_TYPES = [
    "Architect", "Interior Designer", "Contractor", "Builder/Developer",
    "Dealer/Distributor", "Consultant", "Student", "Homeowner",
]


def series_for_tier(tier: int) -> str:
    """Metro shows run in tier 1 cities; regional shows in tier 2/3 markets."""
    return "metro" if tier == 1 else "regional"


def city_alias_map() -> dict[str, str]:
    """Lower-cased raw spelling -> IATA city code."""
    aliases: dict[str, str] = {}
    for name, code, _state, _tier, _region, variants, _first in CITIES:
        for v in [name, code, *variants]:
            aliases[v.strip().lower()] = code
    return aliases
