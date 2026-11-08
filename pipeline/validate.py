"""Data-quality gate. 'error' checks stop the load; 'warning' checks are only logged."""
from __future__ import annotations

import logging

import pandas as pd

log = logging.getLogger(__name__)

PRIMARY_KEYS = {
    "dim_city": ["city_id"],
    "dim_event": ["event_id"],
    "dim_exhibitor": ["exhibitor_id"],
    "dim_visitor": ["visitor_id"],
    "fact_participation": ["event_id", "exhibitor_id"],
    "fact_registrations": ["registration_id"],
    "fact_scans": ["scan_id"],
}

NOT_NULL = {
    "dim_event": ["city_id", "start_date", "end_date"],
    "fact_registrations": ["event_id", "visitor_id", "registered_at_ist", "channel"],
    "fact_scans": ["registration_id", "event_id", "visitor_id", "scanned_at_ist"],
}

# (child table, child column, parent table, parent column)
FOREIGN_KEYS = [
    ("dim_event", "city_id", "dim_city", "city_id"),
    ("fact_participation", "event_id", "dim_event", "event_id"),
    ("fact_participation", "exhibitor_id", "dim_exhibitor", "exhibitor_id"),
    ("fact_registrations", "event_id", "dim_event", "event_id"),
    ("fact_registrations", "visitor_id", "dim_visitor", "visitor_id"),
    ("fact_scans", "registration_id", "fact_registrations", "registration_id"),
    ("fact_scans", "exhibitor_id", "dim_exhibitor", "exhibitor_id"),
]


class DataQualityError(RuntimeError):
    pass


def _result(check, table, failed, severity="error", detail=""):
    return {"check_name": check, "table_name": table, "failed_rows": int(failed),
            "severity": severity, "passed": failed == 0, "detail": detail}


def run_checks(t: dict[str, pd.DataFrame], raw_scan_count: int) -> list[dict]:
    results = []
    for table, cols in PRIMARY_KEYS.items():
        results.append(_result("primary_key_unique", table, t[table].duplicated(cols).sum(), detail=",".join(cols)))
    for table, cols in NOT_NULL.items():
        for c in cols:
            results.append(_result(f"not_null:{c}", table, t[table][c].isna().sum()))
    for child, ccol, parent, pcol in FOREIGN_KEYS:
        vals = t[child][ccol].dropna()
        results.append(_result(f"fk:{ccol}->{parent}", child, (~vals.isin(t[parent][pcol])).sum()))

    regs = t["fact_registrations"]
    results.append(_result("non_empty", "fact_registrations", int(regs.empty)))
    rate = regs["attended"].mean() if len(regs) else 0
    results.append(_result("attendance_rate_between_20_and_95_pct", "fact_registrations",
                           0 if 0.20 <= rate <= 0.95 else len(regs), detail=f"{rate:.1%}"))

    q = t["quarantine"]
    scan_q = (q["source"] == "scans").sum()
    share = scan_q / raw_scan_count if raw_scan_count else 0
    results.append(_result("scan_quarantine_below_5_pct", "fact_scans", scan_q if share >= 0.05 else 0,
                           detail=f"{share:.2%} quarantined"))
    results.append(_result("lead_days_not_negative_unless_onsite", "fact_registrations",
                           ((regs["lead_days"] < 0) & (regs["lead_bucket"] != "onsite")).sum(), severity="warning"))
    return results


def enforce(results: list[dict]) -> None:
    for r in results:
        if not r["passed"]:
            level = logging.ERROR if r["severity"] == "error" else logging.WARNING
            log.log(level, "DQ %s: %s.%s (%d rows) %s", r["severity"], r["table_name"],
                    r["check_name"], r["failed_rows"], r["detail"])
    errors = [r for r in results if r["severity"] == "error" and not r["passed"]]
    log.info("data quality: %d checks, %d errors, %d warnings", len(results), len(errors),
             sum(1 for r in results if r["severity"] == "warning" and not r["passed"]))
    if errors:
        raise DataQualityError(f"{len(errors)} data-quality checks failed; load aborted")
