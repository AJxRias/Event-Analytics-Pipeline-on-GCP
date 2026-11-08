#!/usr/bin/env python3
"""
Synthetic data generator for a trade-exhibition business (metro + regional show series).

It writes the raw "source system" exports that the pipeline ingests:

    data/raw/cities.csv                    reference list kept by ops
    data/raw/events.csv                    event calendar (ops team)
    data/raw/exhibitors.csv                exhibitor company master (sales CRM)
    data/raw/exhibitor_participation.csv   stall bookings per event (sales)
    data/raw/visitors.csv                  visitor CRM export
    data/raw/registrations.csv             registration-platform export
    data/api_source/scans.jsonl            records served by the mock badge-scanning API

The data has realistic behaviour baked in (tier 2/3 markets growing faster, walk-ins,
early-bird no-shows, peak hours, industry affinity at booths) and is deliberately
messy (city spelling variants, duplicate people, mixed date formats, double-submitted
forms, duplicate scans, orphan badges, test accounts) so the pipeline has real work to do.

Usage:  python data_gen/generate.py --scale 1.0 --seed 42
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from faker import Faker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import CITIES, INDUSTRIES, ROOT, VISITOR_TYPES, series_for_tier  # noqa: E402

IND_CODES = list(INDUSTRIES)
LAST_EVENT_DATE = date(2026, 10, 31)

VISITOR_TYPE_P = [0.18, 0.17, 0.15, 0.10, 0.14, 0.08, 0.10, 0.08]
BOOTH_LAMBDA = {  # average booth visits per day by visitor type
    "Architect": 7, "Interior Designer": 7, "Contractor": 5, "Builder/Developer": 6,
    "Dealer/Distributor": 9, "Consultant": 5, "Student": 3, "Homeowner": 4,
}
EMAIL_DOMAINS = ["gmail.com", "yahoo.co.in", "rediffmail.com", "outlook.com"]
BOOTH_SIZES = np.array([9, 12, 18, 27, 36, 54, 72, 108])
BOOTH_SIZE_P = np.array([0.22, 0.20, 0.20, 0.12, 0.12, 0.07, 0.05, 0.02])
ARRIVAL_HOURS = np.arange(10, 18)
ARRIVAL_P = np.array([6, 12, 14, 12, 10, 8, 5, 3], dtype=float)
ARRIVAL_P /= ARRIVAL_P.sum()


class Generator:
    def __init__(self, scale: float, seed: int):
        self.scale = scale
        self.rng = np.random.default_rng(seed)
        self.fake = Faker("en_IN")
        Faker.seed(seed)
        self.city = {c[1]: c for c in CITIES}
        self.visitors: list[dict] = []
        self.city_pool: dict[str, list[int]] = {c[1]: [] for c in CITIES}
        self.registrations: list[dict] = []
        self.market: dict[str, tuple[int, float]] = {}
        self.scans: list[dict] = []
        self._scan_seq = 0

    # ---------------------------------------------------------------- helpers
    def choice(self, items, p=None):
        return items[self.rng.choice(len(items), p=p)]

    def raw_city(self, code: str) -> str:
        name, *_rest = self.city[code]
        variants = self.city[code][5]
        return name if self.rng.random() < 0.8 else self.choice(variants)

    def nearest_weekday(self, d: date, weekday: int) -> date:
        return d + timedelta(days=(weekday - d.weekday()) % 7)

    def messy_phone(self) -> str:
        digits = str(self.rng.integers(6, 10)) + "".join(map(str, self.rng.integers(0, 10, 9)))
        style = self.rng.choice(5, p=[0.35, 0.25, 0.15, 0.15, 0.10])
        return [
            digits,
            f"+91 {digits[:5]} {digits[5:]}",
            f"+91-{digits}",
            f"0{digits}",
            f"91{digits}",
        ][style]

    def messy_datetime(self, dt: datetime) -> str:
        style = self.rng.choice(3, p=[0.6, 0.25, 0.15])
        if style == 0:
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        if style == 1:
            return dt.strftime("%d/%m/%Y %H:%M")
        return dt.strftime("%Y-%m-%dT%H:%M:%S+05:30")

    # ---------------------------------------------------------------- events
    def build_events(self) -> pd.DataFrame:
        rows = []
        metro = [c for c in CITIES if c[3] == 1]
        regional = [c for c in CITIES if c[3] > 1]
        for year in (2024, 2025, 2026):
            for i, c in enumerate(metro):
                shows = [(IND_CODES[i % 8], self.rng.integers(2, 4)),
                         (IND_CODES[(i + 3) % 8], self.rng.integers(8, 11))]
                for ind, month in shows:
                    start = self.nearest_weekday(date(year, int(month), int(self.rng.integers(1, 18))), 3)  # Thu
                    rows.append(self._event_row(c, ind, year, start, 3))
            for i, c in enumerate(regional):
                if year < c[6]:
                    continue
                ind = IND_CODES[(i + 5) % 8]
                start = self.nearest_weekday(date(year, int(self.rng.integers(6, 10)), int(self.rng.integers(1, 20))), 4)  # Fri
                rows.append(self._event_row(c, ind, year, start, 2))
        df = pd.DataFrame(rows)
        df = df[pd.to_datetime(df["end_date"]).dt.date <= LAST_EVENT_DATE]
        return df.sort_values("start_date").reset_index(drop=True)

    def _event_row(self, c, ind, year, start, days) -> dict:
        name, code, _state, tier, *_ = c
        venue = self.choice([f"{name} International Exhibition Centre", f"{name} Trade Centre",
                             f"{name} Convention Grounds"]) if tier == 1 else f"{name} Expo Grounds"
        return {
            "event_code": f"{code}-{ind}-{str(year)[2:]}",
            "event_name": f"{INDUSTRIES[ind]} Expo {name} {year}",
            "city": self.raw_city(code),
            "venue": venue,
            "start_date": start.isoformat(),
            "end_date": (start + timedelta(days=days - 1)).isoformat(),
            "industry": INDUSTRIES[ind],
            "series": series_for_tier(tier),
            "meta_city": code,
            "meta_tier": tier,
            "meta_year": year,
            "meta_first_year": c[6],
            "meta_ind": ind,
        }

    # ---------------------------------------------------------------- exhibitors
    def build_exhibitors(self, n_companies: int = 700) -> pd.DataFrame:
        rows = []
        for i in range(n_companies):
            ind = self.choice(IND_CODES)
            suffix = self.choice(["Industries", "Pvt Ltd", "Systems", "Interiors", "Solutions", "Enterprises", "LLP"])
            rows.append({
                "exhibitor_id": f"EX{1000 + i}",
                "company_name": f"{self.fake.last_name()} {self.choice(['', 'Tech ', 'Build ', 'Design '])}{suffix}".replace("  ", " "),
                "industry": INDUSTRIES[ind],
                "hq_city": self.raw_city(self.choice(list(self.city))),
                "meta_ind": ind,
                "meta_pop": float(self.rng.lognormal(0, 0.6)),
            })
        return pd.DataFrame(rows)

    def build_participation(self, events: pd.DataFrame, ex: pd.DataFrame) -> pd.DataFrame:
        rows = []
        by_ind = {k: g.index.to_numpy() for k, g in ex.groupby("meta_ind")}
        for ev in events.itertuples():
            n = int(self.rng.integers(90, 160) if ev.meta_tier == 1 else self.rng.integers(35, 70))
            n_same = int(n * 0.65)
            same = self.rng.choice(by_ind[ev.meta_ind], size=min(n_same, len(by_ind[ev.meta_ind])), replace=False)
            others = ex.index.difference(same).to_numpy()
            other = self.rng.choice(others, size=n - len(same), replace=False)
            rate = 14000 if ev.meta_tier == 1 else 9000
            for j, idx in enumerate(np.concatenate([same, other])):
                sqm = int(self.rng.choice(BOOTH_SIZES, p=BOOTH_SIZE_P))
                amount = sqm * rate * float(self.rng.uniform(0.85, 1.1))
                amount_raw = f"{amount:,.0f}" if self.rng.random() < 0.3 else f"{amount:.2f}"
                rows.append({
                    "event_code": ev.event_code,
                    "exhibitor_id": ex.at[idx, "exhibitor_id"],
                    "hall": self.choice(["A", "B", "C", "D"] if ev.meta_tier == 1 else ["A", "B"]),
                    "stall_no": f"{j + 1:03d}",
                    "stall_area_sqm": sqm,
                    "package_amount": amount_raw,
                })
        return pd.DataFrame(rows)

    # ---------------------------------------------------------------- visitors
    def new_visitor(self, home: str, interest_ind: str, seen: date) -> int:
        first, last = self.fake.first_name(), self.fake.last_name()
        company = self.fake.company()
        dom_r = self.rng.random()
        if dom_r < 0.35:
            slug = "".join(ch for ch in company.lower() if ch.isalnum())[:14] or "company"
            domain = f"{slug}.in"
        else:
            domain = self.choice(EMAIL_DOMAINS, p=[0.78, 0.12, 0.06, 0.04])
        email = f"{first}.{last}{self.rng.integers(1, 999)}@{domain}".lower()
        vid = len(self.visitors)
        self.visitors.append({
            "_idx": vid,
            "visitor_ref": f"V{100000 + vid}",
            "full_name": f"{first} {last}",
            "email": email,
            "mobile": self.messy_phone(),
            "company": company,
            "designation": self.choice(VISITOR_TYPES, p=VISITOR_TYPE_P),
            "interested_in": INDUSTRIES[interest_ind],
            "city": home,
            "created_on": seen,
            "_ind": interest_ind,
        })
        # engagement: some people come back to many shows
        self.city_pool[home].extend([vid] * int(self.rng.choice([1, 2, 4], p=[0.6, 0.3, 0.1])))
        return vid

    # ---------------------------------------------------------------- registrations + scans
    def build_registrations_and_scans(self, events: pd.DataFrame, part: pd.DataFrame, ex: pd.DataFrame):
        ex_lookup = ex.set_index("exhibitor_id")
        part_by_event = {k: g for k, g in part.groupby("event_code")}
        codes = list(self.city)

        for ev in events.itertuples():
            tier, year = ev.meta_tier, ev.meta_year
            start = date.fromisoformat(ev.start_date)
            n_days = (date.fromisoformat(ev.end_date) - start).days + 1
            # each market has a stable size and its own growth rate; regional markets grow faster
            if ev.meta_city not in self.market:
                size = self.rng.integers(1000, 1600) if tier == 1 else self.rng.integers(280, 480)
                growth = self.rng.normal(1.09, 0.04) if tier == 1 else self.rng.normal(1.26, 0.07)
                self.market[ev.meta_city] = (size, growth)
            size, growth = self.market[ev.meta_city]
            n = int(size * growth ** (year - ev.meta_first_year) * self.rng.uniform(0.92, 1.08) * self.scale)

            # exhibitor weights by visitor interest
            evp = part_by_event[ev.event_code]
            ex_ids = evp["exhibitor_id"].to_numpy()
            base = evp["stall_area_sqm"].to_numpy() ** 0.7 * ex_lookup.loc[ex_ids, "meta_pop"].to_numpy()
            ex_ind = ex_lookup.loc[ex_ids, "meta_ind"].to_numpy()
            weights = {}
            for ind in IND_CODES:
                w = base * np.where(ex_ind == ind, 3.0, 1.0)
                weights[ind] = w / w.sum()

            repeat_p = 0.30 if tier == 1 else 0.22
            walkin_p = 0.12 if tier == 1 else 0.20
            in_event: set[int] = set()
            for _ in range(n):
                pool = self.city_pool[ev.meta_city]
                vid = None
                if pool and self.rng.random() < repeat_p:
                    cand = pool[self.rng.integers(len(pool))]
                    if cand not in in_event:
                        vid = cand
                if vid is None:
                    home = ev.meta_city if self.rng.random() < 0.85 else self.choice(codes)
                    interest = ev.meta_ind if self.rng.random() < 0.6 else self.choice(IND_CODES)
                    vid = self.new_visitor(home, interest, start - timedelta(days=int(self.rng.integers(0, 60))))
                in_event.add(vid)
                v = self.visitors[vid]

                # --- registration timing & channel
                walkin = self.rng.random() < walkin_p
                if walkin:
                    day = int(self.rng.choice(n_days))
                    reg_at = datetime.combine(start + timedelta(days=day), datetime.min.time()) + timedelta(
                        hours=int(self.rng.integers(10, 17)), minutes=int(self.rng.integers(0, 60)))
                    lead = -day
                    channel = "onsite"
                else:
                    bucket = self.rng.choice(3, p=[0.35, 0.40, 0.25])
                    lead = int([self.rng.integers(31, 91), self.rng.integers(8, 31), self.rng.integers(1, 8)][bucket])
                    reg_at = datetime.combine(start - timedelta(days=lead), datetime.min.time()) + timedelta(
                        hours=int(self.rng.integers(7, 24)), minutes=int(self.rng.integers(0, 60)), seconds=int(self.rng.integers(0, 60)))
                    wa = 0.32 if tier > 1 else 0.22
                    channel = self.choice(["website", "whatsapp", "email_campaign", "partner_association", "social_ads"],
                                          p=[0.62 - wa, wa, 0.18, 0.12, 0.08])

                # --- ticket
                t = self.rng.random()
                if t < 0.85:
                    ticket, amount = "free_trade_visitor", 0
                elif t < 0.95:
                    ticket, amount = "delegate_pass", 1500 if lead > 30 else 2500
                else:
                    ticket, amount = "conference_pass", 4000 if lead > 30 else 6000

                # --- attendance (ground truth, observed later only through entry scans)
                if walkin:
                    p_attend = 1.0
                else:
                    p_attend = 0.45 if lead > 30 else (0.60 if lead > 7 else 0.75)
                    p_attend += 0.20 if amount > 0 else 0
                    p_attend += 0.05 if channel == "whatsapp" else 0
                    p_attend -= 0.08 if tier > 1 else 0
                attended = self.rng.random() < min(p_attend, 0.98)

                reg_id = f"R{1000000 + len(self.registrations)}"
                self.registrations.append(self._registration_row(reg_id, ev.event_code, v, reg_at, channel, ticket, amount))

                # double-submitted form (same person, same event, a few minutes later)
                if self.rng.random() < 0.012:
                    dup_id = f"R{1000000 + len(self.registrations)}"
                    self.registrations.append(self._registration_row(
                        dup_id, ev.event_code, v, reg_at + timedelta(minutes=int(self.rng.integers(1, 15))), channel, ticket, amount))

                if attended:
                    if walkin:
                        days = [day]
                    else:
                        k = int(self.rng.choice([1, 2, 3], p=[0.70, 0.25, 0.05]))
                        days = sorted(self.rng.choice(n_days, size=min(k, n_days), replace=False).tolist())
                    self._scans_for_visit(ev.event_code, reg_id, v, start, days, ex_ids, weights[v["_ind"]], walkin, reg_at)

        # orphan badges the vendor could not reconcile
        for _ in range(int(len(self.scans) * 0.003)):
            s = dict(self.scans[self.rng.integers(len(self.scans))])
            s["scan_id"] = self._next_scan_id()
            s["badge_id"] = f"BDG-R9{self.rng.integers(100000, 999999)}"
            self.scans.append(s)

    def _registration_row(self, reg_id, event_code, v, reg_at, channel, ticket, amount) -> dict:
        ref = v["visitor_ref"]
        # registrations sometimes point at a duplicate CRM record of the same person
        if "_dups" in v and self.rng.random() < 0.5:
            ref = self.choice(v["_dups"])
        if amount == 0:
            amount_raw = self.choice(["0", "", "0.00"])
        else:
            amount_raw = self.choice([str(amount), f"₹{amount:,}", f"{amount}.00", f"INR {amount}"])
            if self.rng.random() < 0.002:
                amount_raw = f"-{amount}"  # bad refund entry
        return {
            "reg_id": reg_id,
            "event_code": event_code,
            "visitor_ref": ref,
            "registered_on": self.messy_datetime(reg_at),
            "source_channel": channel,
            "pass_type": ticket,
            "amount_paid": amount_raw,
        }

    def _next_scan_id(self) -> str:
        self._scan_seq += 1
        return f"SCN{self._scan_seq:09d}"

    def _ts(self, dt: datetime) -> str:
        if self.rng.random() < 0.1:  # some devices report UTC
            return (dt - timedelta(hours=5, minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
        return dt.strftime("%Y-%m-%dT%H:%M:%S+05:30")

    def _scans_for_visit(self, event_code, reg_id, v, start, days, ex_ids, w, walkin, reg_at):
        badge = f"BDG-{reg_id}"
        lam = BOOTH_LAMBDA[v["designation"]]
        for d in days:
            day0 = datetime.combine(start + timedelta(days=int(d)), datetime.min.time())
            if walkin:
                arrive = reg_at + timedelta(minutes=int(self.rng.integers(2, 20)))
            else:
                arrive = day0 + timedelta(hours=int(self.rng.choice(ARRIVAL_HOURS, p=ARRIVAL_P)),
                                          minutes=int(self.rng.integers(0, 60)), seconds=int(self.rng.integers(0, 60)))
            self.scans.append({"scan_id": self._next_scan_id(), "badge_id": badge, "event_code": event_code,
                               "scan_type": "ENTRY", "exhibitor_id": None, "scanned_at": self._ts(arrive),
                               "device_id": f"GATE-{self.rng.integers(1, 7):02d}"})
            close = day0 + timedelta(hours=19)
            span = (close - arrive).total_seconds()
            if span <= 600:
                continue
            k = min(int(self.rng.poisson(lam)) + 1, len(ex_ids))
            visited = self.rng.choice(ex_ids, size=k, replace=False, p=w)
            offsets = np.sort(self.rng.uniform(300, span, size=k))
            for exh, off in zip(visited, offsets):
                t = arrive + timedelta(seconds=float(off))
                self.scans.append({"scan_id": self._next_scan_id(), "badge_id": badge, "event_code": event_code,
                                   "scan_type": "BOOTH", "exhibitor_id": exh, "scanned_at": self._ts(t),
                                   "device_id": f"LEAD-{exh}"})
                if self.rng.random() < 0.02:  # exhibitor scanned the same badge twice
                    t2 = t + timedelta(seconds=int(self.rng.integers(5, 60)))
                    self.scans.append({"scan_id": self._next_scan_id(), "badge_id": badge, "event_code": event_code,
                                       "scan_type": "BOOTH", "exhibitor_id": exh, "scanned_at": self._ts(t2),
                                       "device_id": f"LEAD-{exh}"})

    # ---------------------------------------------------------------- CRM mess
    def add_duplicate_people(self):
        """~3% of people exist twice in the CRM (different ref, email in different case)."""
        n = len(self.visitors)
        for idx in self.rng.choice(n, size=int(n * 0.03), replace=False):
            v = self.visitors[idx]
            dup = dict(v)
            dup_ref = f"V{100000 + len(self.visitors)}"
            dup.update({"_idx": len(self.visitors), "visitor_ref": dup_ref,
                        "email": f"  {v['email'].upper()} " if self.rng.random() < 0.5 else v["email"].title(),
                        "mobile": self.messy_phone() if self.rng.random() < 0.3 else v["mobile"],
                        "created_on": v["created_on"] + timedelta(days=int(self.rng.integers(1, 200)))})
            dup.pop("_dups", None)
            v.setdefault("_dups", []).append(dup_ref)
            self.visitors.append(dup)

    def visitors_frame(self) -> pd.DataFrame:
        rows = []
        for v in self.visitors:
            r = {k: v[k] for k in ["visitor_ref", "full_name", "email", "mobile", "company",
                                   "designation", "interested_in", "city", "created_on"]}
            r["city"] = self.raw_city(r["city"])
            r["created_on"] = (r["created_on"].strftime("%d-%m-%Y") if self.rng.random() < 0.3
                               else r["created_on"].isoformat())
            if self.rng.random() < 0.01:
                r["email"] = ""
            rows.append(r)
        # test accounts created by the web team
        for i in range(25):
            rows.append({"visitor_ref": f"VT{i:03d}", "full_name": "Test User", "email": f"test{i}@example.com",
                         "mobile": "9999999999", "company": "QA", "designation": "Other", "interested_in": "",
                         "city": "Mumbai", "created_on": "2024-01-01"})
        return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scale", type=float, default=1.0, help="multiplier on registration volume")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", type=Path, default=ROOT / "data")
    args = ap.parse_args()

    g = Generator(args.scale, args.seed)
    events = g.build_events()
    exhibitors = g.build_exhibitors()
    participation = g.build_participation(events, exhibitors)

    # pre-create the duplicate CRM records' targets lazily: duplicates are added after
    # a first pass of visitors exists, so run registrations in two halves.
    first, second = events.iloc[: len(events) // 2], events.iloc[len(events) // 2:]
    g.build_registrations_and_scans(first, participation, exhibitors)
    g.add_duplicate_people()
    g.build_registrations_and_scans(second, participation, exhibitors)

    # test accounts also registered for a show
    test_rows = [{"reg_id": f"RT{i:04d}", "event_code": events.iloc[-1]["event_code"], "visitor_ref": f"VT{i % 25:03d}",
                  "registered_on": "2026-09-01 10:00:00", "source_channel": "website",
                  "pass_type": "free_trade_visitor", "amount_paid": "0"} for i in range(40)]

    raw, api = args.out / "raw", args.out / "api_source"
    raw.mkdir(parents=True, exist_ok=True)
    api.mkdir(parents=True, exist_ok=True)

    cities = pd.DataFrame([{"city": c[0], "city_code": c[1], "state": c[2], "tier": c[3], "region": c[4]} for c in CITIES])
    cities.to_csv(raw / "cities.csv", index=False)
    events[[c for c in events.columns if not c.startswith("meta_")]].to_csv(raw / "events.csv", index=False)
    exhibitors[[c for c in exhibitors.columns if not c.startswith("meta_")]].to_csv(raw / "exhibitors.csv", index=False)
    participation.to_csv(raw / "exhibitor_participation.csv", index=False)
    g.visitors_frame().to_csv(raw / "visitors.csv", index=False)
    regs = pd.DataFrame(g.registrations + test_rows)
    regs.sample(frac=1, random_state=args.seed).to_csv(raw / "registrations.csv", index=False)  # exports aren't sorted

    order = g.rng.permutation(len(g.scans))  # vendor returns scans in arbitrary order
    with open(api / "scans.jsonl", "w") as f:
        for i in order:
            f.write(json.dumps(g.scans[i]) + "\n")

    print(f"events={len(events)} exhibitors={len(exhibitors)} participation={len(participation)} "
          f"visitors={len(g.visitors) + 25} registrations={len(regs)} scans={len(g.scans)}")
    print(f"raw files -> {raw}\napi source -> {api / 'scans.jsonl'}")


if __name__ == "__main__":
    main()
