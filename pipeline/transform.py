"""
Transform step: clean, standardise, de-duplicate and model the raw data into a star schema.

Output tables
  dim_city, dim_event, dim_exhibitor, dim_visitor        dimensions
  fact_participation, fact_registrations, fact_scans     facts
  quarantine                                             rows rejected, with a reason

All timestamps are converted to Indian Standard Time and stored as naive datetimes
(BigQuery DATETIME), so analysts never have to think about time zones.
"""
from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from config import city_alias_map, series_for_tier

log = logging.getLogger(__name__)

PII_SALT = os.getenv("PII_SALT", "change-me-in-prod").strip()
TEST_EMAIL_DOMAINS = ("example.com", "test.com", "mailinator.com")
BOOTH_DUP_WINDOW_SECONDS = 120


# ----------------------------------------------------------------------------- helpers
def normalize_city(raw: pd.Series) -> pd.Series:
    """Map messy spellings ('Bombay', 'mumbai ', 'Navi Mumbai') to an IATA city code."""
    aliases = city_alias_map()
    return raw.fillna("").str.strip().str.lower().map(aliases)


def normalize_phone(raw: pd.Series) -> pd.Series:
    """Normalise Indian mobile numbers to E.164 (+91XXXXXXXXXX); invalid -> NaN."""
    digits = raw.fillna("").str.replace(r"\D", "", regex=True)
    digits = digits.where(~((digits.str.len() == 12) & digits.str.startswith("91")), digits.str[2:])
    digits = digits.where(~((digits.str.len() == 11) & digits.str.startswith("0")), digits.str[1:])
    valid = digits.str.fullmatch(r"[6-9]\d{9}")
    return ("+91" + digits).where(valid)


def normalize_email(raw: pd.Series) -> pd.Series:
    e = raw.fillna("").str.strip().str.lower()
    return e.where(e.str.contains("@", regex=False))


def parse_mixed_datetime(raw: pd.Series, formats: list[str]) -> pd.Series:
    """Try each known format in turn (no guessing, so 03/04 is never silently swapped)."""
    s = raw.fillna("").str.strip().str.replace(r"\+05:30$", "", regex=True)
    out = pd.Series(pd.NaT, index=raw.index, dtype="datetime64[ns]")
    for fmt in formats:
        missing = out.isna()
        if not missing.any():
            break
        out[missing] = pd.to_datetime(s[missing], format=fmt, errors="coerce")
    return out


def parse_iso_to_ist(raw: pd.Series) -> pd.Series:
    """'...+05:30' stays as is; '...Z' (UTC) is shifted by +5:30."""
    s = raw.fillna("")
    base = pd.to_datetime(s.str[:19], format="%Y-%m-%dT%H:%M:%S", errors="coerce")
    is_utc = s.str.endswith("Z")
    return base + pd.to_timedelta(np.where(is_utc, 330, 0), unit="m")


def parse_amount(raw: pd.Series) -> pd.Series:
    """'₹1,500' / 'INR 1500' / '1500.00' / '' -> float (empty means free)."""
    s = raw.fillna("").str.replace(r"(?i)inr|₹|,|\s", "", regex=True)
    s = s.where(s != "", "0")
    return pd.to_numeric(s, errors="coerce")


def sha256(values: pd.Series) -> pd.Series:
    return values.map(lambda v: hashlib.sha256(f"{PII_SALT}{v}".encode()).hexdigest() if isinstance(v, str) else None)


# ----------------------------------------------------------------------------- transformer
@dataclass
class TransformResult:
    tables: dict[str, pd.DataFrame]
    issues: list[dict] = field(default_factory=list)  # warnings for the DQ log


class Transformer:
    def __init__(self, raw: dict[str, pd.DataFrame], scans: pd.DataFrame):
        self.raw = raw
        self.scans_raw = scans
        self.quarantine: list[pd.DataFrame] = []
        self.issues: list[dict] = []

    def _reject(self, source: str, ids: pd.Series, reason: str):
        if len(ids):
            self.quarantine.append(pd.DataFrame({"source": source, "record_id": ids.astype(str).values, "reason": reason}))
            log.info("  quarantined %6d %-14s rows: %s", len(ids), source, reason)

    def _issue(self, table: str, rule: str, rows: int):
        if rows:
            self.issues.append({"table_name": table, "check_name": rule, "failed_rows": int(rows), "severity": "warning"})

    # ---- dimensions
    def dim_city(self) -> pd.DataFrame:
        c = self.raw["cities"]
        out = pd.DataFrame({
            "city_id": c["city_code"].str.strip(),
            "city": c["city"].str.strip(),
            "state": c["state"].str.strip(),
            "tier": c["tier"].astype(int),
            "region": c["region"].str.strip(),
        })
        out["series"] = out["tier"].map(series_for_tier)
        return out

    def dim_event(self) -> pd.DataFrame:
        e = self.raw["events"]
        out = pd.DataFrame({
            "event_id": e["event_code"].str.strip(),
            "event_name": e["event_name"].str.strip(),
            "industry": e["industry"].str.strip(),
            "city_id": normalize_city(e["city"]),
            "venue": e["venue"].str.strip(),
            "start_date": pd.to_datetime(e["start_date"], errors="coerce").dt.date,
            "end_date": pd.to_datetime(e["end_date"], errors="coerce").dt.date,
            "series": e["series"].str.strip().str.lower(),
        })
        bad = out["city_id"].isna() | out["start_date"].isna() | out["end_date"].isna()
        self._reject("events", out.loc[bad, "event_id"], "unknown city or invalid dates")
        out = out[~bad].copy()
        out["duration_days"] = (pd.to_datetime(out["end_date"]) - pd.to_datetime(out["start_date"])).dt.days + 1
        out["event_year"] = pd.to_datetime(out["start_date"]).dt.year
        return out.reset_index(drop=True)

    def dim_exhibitor(self) -> pd.DataFrame:
        x = self.raw["exhibitors"]
        out = pd.DataFrame({
            "exhibitor_id": x["exhibitor_id"].str.strip(),
            "company_name": x["company_name"].str.strip().str.replace(r"\s+", " ", regex=True),
            "industry": x["industry"].str.strip(),
            "hq_city_id": normalize_city(x["hq_city"]),
        })
        return out.drop_duplicates("exhibitor_id").reset_index(drop=True)

    def fact_participation(self, events: pd.DataFrame, exhibitors: pd.DataFrame) -> pd.DataFrame:
        p = self.raw["exhibitor_participation"]
        out = pd.DataFrame({
            "event_id": p["event_code"].str.strip(),
            "exhibitor_id": p["exhibitor_id"].str.strip(),
            "hall": p["hall"].str.strip().str.upper(),
            "booth_no": p["hall"].str.strip().str.upper() + "-" + p["stall_no"].str.strip(),
            "booth_sqm": pd.to_numeric(p["stall_area_sqm"], errors="coerce"),
            "package_amount_inr": parse_amount(p["package_amount"]).round(2),
        })
        bad = ~out["event_id"].isin(events["event_id"]) | ~out["exhibitor_id"].isin(exhibitors["exhibitor_id"])
        self._reject("participation", out.loc[bad, "event_id"] + "/" + out.loc[bad, "exhibitor_id"], "unknown event or exhibitor")
        return out[~bad].reset_index(drop=True)

    def dim_visitor(self) -> tuple[pd.DataFrame, pd.Series]:
        """De-duplicate people (same email, or same phone when email is missing).

        Returns the dimension and a mapping raw visitor_ref -> canonical visitor_id.
        """
        v = self.raw["visitors"].copy()
        v["email_norm"] = normalize_email(v["email"])
        v["phone_e164"] = normalize_phone(v["mobile"])
        v["created"] = parse_mixed_datetime(v["created_on"], ["%Y-%m-%d", "%d-%m-%Y"]).dt.date

        is_test = (v["email_norm"].fillna("").str.endswith(TEST_EMAIL_DOMAINS)
                   | v["full_name"].str.strip().str.lower().eq("test user"))
        self._reject("visitors", v.loc[is_test, "visitor_ref"], "test account")
        v = v[~is_test]

        self._issue("visitors", "missing_email", v["email_norm"].isna().sum())
        self._issue("visitors", "invalid_phone", v["phone_e164"].isna().sum())

        v["person_key"] = v["email_norm"].fillna(v["phone_e164"]).fillna(v["visitor_ref"])
        v = v.sort_values(["created", "visitor_ref"])
        v["visitor_id"] = v.groupby("person_key")["visitor_ref"].transform("first")
        n_dupes = (v["visitor_ref"] != v["visitor_id"]).sum()
        log.info("  merged %d duplicate visitor records into existing people", n_dupes)
        self._issue("visitors", "duplicate_person_merged", n_dupes)
        mapping = v.set_index("visitor_ref")["visitor_id"]

        first = v.drop_duplicates("visitor_id", keep="first")
        first_seen = v.groupby("visitor_id")["created"].min()
        dim = pd.DataFrame({
            "visitor_id": first["visitor_id"].values,
            "email_sha256": sha256(first["email_norm"]).values,
            "phone_last4": first["phone_e164"].str[-4:].values,
            "visitor_type": first["designation"].str.strip().values,
            "interest_industry": first["interested_in"].str.strip().replace("", None).values,
            "home_city_id": normalize_city(first["city"]).values,
            "company": first["company"].str.strip().values,
        })
        dim["first_seen_date"] = dim["visitor_id"].map(first_seen)
        return dim.reset_index(drop=True), mapping

    # ---- facts
    def fact_registrations(self, events: pd.DataFrame, visitor_map: pd.Series) -> tuple[pd.DataFrame, pd.Series]:
        r = self.raw["registrations"]
        out = pd.DataFrame({
            "registration_id": r["reg_id"].str.strip(),
            "event_id": r["event_code"].str.strip(),
            "visitor_ref": r["visitor_ref"].str.strip(),
            "registered_at_ist": parse_mixed_datetime(
                r["registered_on"], ["%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%d/%m/%Y %H:%M"]),
            "channel": r["source_channel"].str.strip().str.lower(),
            "ticket_type": r["pass_type"].str.strip().str.lower(),
            "amount_inr": parse_amount(r["amount_paid"]),
        })
        out["visitor_id"] = out["visitor_ref"].map(visitor_map)

        for rule, reason in [
            (lambda d: d["visitor_id"].isna(), "unknown or test visitor"),
            (lambda d: ~d["event_id"].isin(events["event_id"]), "unknown event"),
            (lambda d: d["registered_at_ist"].isna(), "unparseable registration timestamp"),
        ]:
            mask = rule(out)
            self._reject("registrations", out.loc[mask, "registration_id"], reason)
            out = out[~mask]

        neg = out["amount_inr"] < 0
        self._issue("registrations", "negative_amount_set_null", neg.sum())
        out.loc[neg, "amount_inr"] = np.nan

        # double-submitted forms (and duplicates exposed by the person merge): keep the first
        out = out.sort_values(["event_id", "visitor_id", "registered_at_ist"])
        keep_id = out.groupby(["event_id", "visitor_id"])["registration_id"].transform("first")
        reg_alias = pd.Series(keep_id.values, index=out["registration_id"].values)  # any reg id -> kept reg id
        dup = out["registration_id"] != keep_id
        self._issue("registrations", "duplicate_registration_removed", dup.sum())
        out = out[~dup].copy()

        ev = events.set_index("event_id")
        start = pd.to_datetime(out["event_id"].map(ev["start_date"]))
        out["registration_date"] = out["registered_at_ist"].dt.date
        out["lead_days"] = (start - out["registered_at_ist"].dt.normalize()).dt.days
        out["lead_bucket"] = np.select(
            [(out["channel"] == "onsite") | (out["lead_days"] <= 0), out["lead_days"] <= 7, out["lead_days"] <= 30],
            ["onsite", "0-7 days", "8-30 days"], default="31+ days (early bird)")
        return out.drop(columns="visitor_ref").reset_index(drop=True), reg_alias

    def fact_scans(self, events: pd.DataFrame, regs: pd.DataFrame, reg_alias: pd.Series,
                   participation: pd.DataFrame) -> pd.DataFrame:
        s = self.scans_raw
        out = pd.DataFrame({
            "scan_id": s["scan_id"].astype(str),
            "registration_id": s["badge_id"].astype(str).str.removeprefix("BDG-").map(reg_alias),
            "event_id": s["event_code"].astype(str),
            "exhibitor_id": s["exhibitor_id"],
            "scan_type": s["scan_type"].astype(str).str.lower(),
            "scanned_at_ist": parse_iso_to_ist(s["scanned_at"].astype(str)),
            "device_id": s["device_id"],
        })
        out = out.drop_duplicates("scan_id")
        reg_event = regs.set_index("registration_id")["event_id"]
        ev = events.set_index("event_id")
        start = pd.to_datetime(out["event_id"].map(ev["start_date"]))
        end = pd.to_datetime(out["event_id"].map(ev["end_date"])) + pd.Timedelta(days=1)
        booth_ok = (out["event_id"] + "|" + out["exhibitor_id"].fillna("")).isin(
            participation["event_id"] + "|" + participation["exhibitor_id"])

        for mask, reason in [
            (out["registration_id"].isna(), "badge not linked to a registration"),
            (out["registration_id"].map(reg_event) != out["event_id"], "badge belongs to a different event"),
            (out["scanned_at_ist"].isna() | (out["scanned_at_ist"] < start) | (out["scanned_at_ist"] >= end),
             "timestamp outside event dates"),
            ((out["scan_type"] == "booth") & ~booth_ok, "exhibitor not at this event"),
        ]:
            mask = mask.reindex(out.index, fill_value=False)
            self._reject("scans", out.loc[mask, "scan_id"], reason)
            out = out[~mask]

        out["scan_date"] = out["scanned_at_ist"].dt.date
        out["scan_hour"] = out["scanned_at_ist"].dt.hour

        # entry: one per person per day. booth: drop repeat scans of the same badge at the same booth within 2 min
        out = out.sort_values(["registration_id", "scan_type", "exhibitor_id", "scanned_at_ist"])
        entry = out["scan_type"] == "entry"
        dup_entry = entry & out.duplicated(["registration_id", "scan_date", "scan_type"])
        gap = out.groupby(["registration_id", "exhibitor_id"], dropna=False)["scanned_at_ist"].diff().dt.total_seconds()
        dup_booth = ~entry & (gap < BOOTH_DUP_WINDOW_SECONDS)
        self._issue("scans", "duplicate_entry_scan_removed", dup_entry.sum())
        self._issue("scans", "duplicate_booth_scan_removed", dup_booth.sum())
        out = out[~(dup_entry | dup_booth)]

        out["visitor_id"] = out["registration_id"].map(regs.set_index("registration_id")["visitor_id"])
        cols = ["scan_id", "registration_id", "event_id", "visitor_id", "exhibitor_id", "scan_type",
                "scanned_at_ist", "scan_date", "scan_hour", "device_id"]
        return out[cols].sort_values("scanned_at_ist").reset_index(drop=True)

    @staticmethod
    def add_attendance(regs: pd.DataFrame, scans: pd.DataFrame) -> pd.DataFrame:
        entries = scans[scans["scan_type"] == "entry"].groupby("registration_id").agg(
            first_entry_at_ist=("scanned_at_ist", "min"), days_attended=("scan_date", "nunique"))
        regs = regs.join(entries, on="registration_id")
        regs["attended"] = regs["first_entry_at_ist"].notna()
        regs["days_attended"] = regs["days_attended"].fillna(0).astype(int)
        return regs

    # ---- orchestrate
    def run(self) -> TransformResult:
        log.info("transforming dimensions")
        city = self.dim_city()
        events = self.dim_event()
        exhibitors = self.dim_exhibitor()
        participation = self.fact_participation(events, exhibitors)
        visitors, vmap = self.dim_visitor()
        log.info("transforming registrations")
        regs, reg_alias = self.fact_registrations(events, vmap)
        log.info("transforming %d scans", len(self.scans_raw))
        scans = self.fact_scans(events, regs, reg_alias, participation)
        regs = self.add_attendance(regs, scans)

        reg_cols = ["registration_id", "event_id", "visitor_id", "registered_at_ist", "registration_date", "lead_days",
                    "lead_bucket", "channel", "ticket_type", "amount_inr", "attended", "first_entry_at_ist", "days_attended"]
        quarantine = (pd.concat(self.quarantine, ignore_index=True) if self.quarantine
                      else pd.DataFrame(columns=["source", "record_id", "reason"]))
        tables = {
            "dim_city": city,
            "dim_event": events,
            "dim_exhibitor": exhibitors,
            "dim_visitor": visitors,
            "fact_participation": participation,
            "fact_registrations": regs[reg_cols],
            "fact_scans": scans,
            "quarantine": quarantine,
        }
        for name, df in tables.items():
            log.info("  %-20s %8d rows", name, len(df))
        return TransformResult(tables=tables, issues=self.issues)
