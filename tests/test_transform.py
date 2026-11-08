import pandas as pd

from pipeline.transform import (normalize_city, normalize_phone, parse_amount, parse_iso_to_ist,
                                parse_mixed_datetime, Transformer)


def test_city_aliases_map_to_one_code():
    raw = pd.Series(["Bombay", " mumbai ", "MUMBAI ", "Bangalore", "B'lore", "Cochin", "Atlantis"])
    assert normalize_city(raw).tolist()[:6] == ["BOM", "BOM", "BOM", "BLR", "BLR", "COK"]
    assert pd.isna(normalize_city(raw).iloc[6])


def test_phone_normalisation():
    raw = pd.Series(["9876543210", "+91 98765 43210", "+91-9876543210", "09876543210", "919876543210", "12345"])
    out = normalize_phone(raw)
    assert out.iloc[:5].eq("+919876543210").all()
    assert pd.isna(out.iloc[5])


def test_mixed_datetime_formats_do_not_swap_day_and_month():
    raw = pd.Series(["2025-03-04 10:00:00", "04/03/2025 10:00", "2025-03-04T10:00:00+05:30", "garbage"])
    out = parse_mixed_datetime(raw, ["%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%d/%m/%Y %H:%M"])
    assert (out.iloc[:3] == pd.Timestamp("2025-03-04 10:00")).all()
    assert pd.isna(out.iloc[3])


def test_utc_scans_are_shifted_to_ist():
    out = parse_iso_to_ist(pd.Series(["2025-03-04T04:30:00Z", "2025-03-04T10:00:00+05:30"]))
    assert (out == pd.Timestamp("2025-03-04 10:00")).all()


def test_amount_parsing():
    out = parse_amount(pd.Series(["₹1,500", "INR 2500", "1500.00", "", "0", "-1500"]))
    assert out.tolist() == [1500, 2500, 1500, 0, 0, -1500]


def _raw():
    cities = pd.DataFrame({"city": ["Mumbai"], "city_code": ["BOM"], "state": ["Maharashtra"], "tier": ["1"], "region": ["West"]})
    events = pd.DataFrame({"event_code": ["BOM-AI-25"], "event_name": ["Expo"], "city": ["Bombay"], "venue": ["V"],
                           "start_date": ["2025-03-06"], "end_date": ["2025-03-08"], "industry": ["Architecture & Interiors"],
                           "series": ["metro"]})
    exhibitors = pd.DataFrame({"exhibitor_id": ["EX1"], "company_name": ["Acme  Ltd"], "industry": ["Kitchen & Bath"], "hq_city": ["Pune"]})
    part = pd.DataFrame({"event_code": ["BOM-AI-25"], "exhibitor_id": ["EX1"], "hall": ["a"], "stall_no": ["001"],
                         "stall_area_sqm": ["18"], "package_amount": ["2,52,000"]})
    visitors = pd.DataFrame({
        "visitor_ref": ["V1", "V2", "VT1"],
        "full_name": ["Asha Rao", "Asha Rao", "Test User"],
        "email": ["asha@x.in", "  ASHA@X.IN ", "test1@example.com"],
        "mobile": ["9876543210", "+91 98765 43210", "9999999999"],
        "company": ["X", "X", "QA"], "designation": ["Architect"] * 3, "interested_in": ["Kitchen & Bath", "", ""],
        "city": ["Mumbai", "Bombay", "Mumbai"], "created_on": ["2025-01-01", "05-02-2025", "2024-01-01"],
    })
    regs = pd.DataFrame({
        "reg_id": ["R1", "R2", "R3"], "event_code": ["BOM-AI-25"] * 3, "visitor_ref": ["V1", "V2", "VT1"],
        "registered_on": ["2025-02-01 10:00:00", "01/02/2025 10:05", "2025-02-01 11:00:00"],
        "source_channel": ["website"] * 3, "pass_type": ["free_trade_visitor"] * 3, "amount_paid": ["0", "", "0"],
    })
    scans = pd.DataFrame({
        "scan_id": ["S1", "S2", "S3", "S4", "S5"],
        "badge_id": ["BDG-R1", "BDG-R2", "BDG-R1", "BDG-R1", "BDG-R999"],
        "event_code": ["BOM-AI-25"] * 5,
        "scan_type": ["ENTRY", "BOOTH", "BOOTH", "BOOTH", "ENTRY"],
        "exhibitor_id": [None, "EX1", "EX1", "EX1", None],
        "scanned_at": ["2025-03-06T11:00:00+05:30", "2025-03-06T06:00:00Z", "2025-03-06T11:30:30+05:30",
                       "2025-03-06T13:00:00+05:30", "2025-03-06T12:00:00+05:30"],
        "device_id": ["GATE-01", "LEAD-EX1", "LEAD-EX1", "LEAD-EX1", "GATE-02"],
    })
    return {"cities": cities, "events": events, "exhibitors": exhibitors, "exhibitor_participation": part,
            "visitors": visitors, "registrations": regs}, scans


def test_end_to_end_transform_dedupes_people_registrations_and_scans():
    raw, scans = _raw()
    t = Transformer(raw, scans).run().tables

    assert t["dim_visitor"]["visitor_id"].tolist() == ["V1"]              # V2 merged into V1, test user dropped
    regs = t["fact_registrations"]
    assert regs["registration_id"].tolist() == ["R1"]                     # R2 was a duplicate of R1
    assert bool(regs.loc[0, "attended"]) and regs.loc[0, "lead_days"] == 33

    s = t["fact_scans"]
    # S2 (from the duplicate registration, UTC 06:00 = 11:30 IST) is kept and re-pointed to R1;
    # S3 at 11:30:30 is a repeat scan within 2 minutes; S5 is an orphan badge
    assert sorted(s["scan_id"]) == ["S1", "S2", "S4"]
    assert set(s["registration_id"]) == {"R1"}
    q = t["quarantine"]
    assert set(q["record_id"]) == {"VT1", "R3", "S5"}
    assert t["fact_participation"].loc[0, "package_amount_inr"] == 252000
