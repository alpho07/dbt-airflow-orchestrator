"""
Simulated EMR data generator for emr_source.emr.

Usage:
    python emr_generator.py init [--days 180] [--patients 3000] [--end-date YYYY-MM-DD] [--seed 42]
"""
from __future__ import annotations

import argparse
import os
import random
from datetime import date, datetime, time, timedelta, timezone
import json

import psycopg2
from psycopg2.extras import execute_values

EAT = timezone(timedelta(hours=3))

DB = {
    "host": os.getenv("EMR_PG_HOST", "localhost"),
    "port": int(os.getenv("EMR_PG_PORT", "5433")),
    "dbname": os.getenv("EMR_PG_DB", "emr_source"),
    "user": os.getenv("EMR_PG_USER", "dw_user"),
    "password": os.getenv("EMR_PG_PASSWORD"),  # None -> libpq falls back to ~/.pgpass
}

COUNTIES = {
    "Nairobi": ["Westlands", "Embakasi East", "Kibra", "Kasarani"],
    "Kisumu": ["Kisumu Central", "Nyando", "Muhoroni"],
    "Mombasa": ["Mvita", "Likoni", "Kisauni"],
    "Nakuru": ["Nakuru Town East", "Naivasha", "Molo"],
    "Kakamega": ["Lurambi", "Mumias West", "Shinyalu"],
    "Homa Bay": ["Rachuonyo North", "Ndhiwa", "Suba South"],
    "Kiambu": ["Thika Town", "Ruiru", "Limuru"],
    "Turkana": ["Turkana Central", "Loima", "Turkana West"],
}
LEVEL_SUFFIX = {2: "Dispensary", 3: "Health Centre", 4: "Sub-County Hospital", 5: "County Referral Hospital"}
PREFIXES = ["St. Joseph", "Upper", "Lower", "Mission", "Central", "New"]
OWNERSHIP = ["MOH", "MOH", "MOH", "FBO", "Private", "NGO"]
FIRST_F = ["Achieng", "Akinyi", "Wanjiku", "Njeri", "Mwende", "Chebet", "Nafula",
           "Atieno", "Wairimu", "Zawadi", "Amina", "Faith", "Grace", "Mercy"]
FIRST_M = ["Otieno", "Odhiambo", "Kamau", "Mwangi", "Kiprono", "Wafula",
           "Mutua", "Juma", "Baraka", "Brian", "Kevin", "Hassan"]
LAST = ["Ochieng", "Onyango", "Kariuki", "Njoroge", "Kiptoo", "Wekesa", "Musyoka",
        "Omondi", "Mohamed", "Barasa", "Ekai", "Lokol", "Wambua", "Owino"]

TABLES = [
    ("facilities", "facility_id",
     "facility_id, mfl_code, facility_name, county, sub_county, keph_level, ownership, is_active, created_at, updated_at"),
    ("patients", "patient_id",
     "patient_id, facility_id, upi, first_name, last_name, sex, date_of_birth, phone, created_at, updated_at, deleted_at"),
    ("visits", "visit_id",
     "visit_id, patient_id, facility_id, visit_date, visit_type, anc_visit_no, created_at, updated_at"),
    ("lab_results", "lab_result_id",
     "lab_result_id, visit_id, test_code, result_value, result_date, created_at, updated_at"),
]


# ---------- helpers ----------
def ts(d: date) -> datetime:
    """Random timestamp during clinic hours (EAT) on day d."""
    return datetime.combine(d, time(random.randint(8, 16), random.randint(0, 59), random.randint(0, 59)), tzinfo=EAT)


def rand_date(a: date, b: date) -> date:
    return a + timedelta(days=random.randint(0, max((b - a).days, 0)))


def dirty_county(c: str) -> str | None:
    r = random.random()
    if r < 0.08:
        return c.upper()
    if r < 0.14:
        return c.lower() + " "
    if r < 0.17:
        return None
    return c


def dirty_sex(s: str) -> str | None:
    r = random.random()
    if r < 0.60:
        return s
    if r < 0.85:
        return {"F": "Female", "M": "Male"}[s]
    if r < 0.97:
        return {"F": "female", "M": "male"}[s]
    return None


def phone() -> str | None:
    digits = f"7{random.randint(0, 99_999_999):08d}"
    r = random.random()
    if r < 0.55:
        return "0" + digits
    if r < 0.85:
        return "+254" + digits
    if r < 0.93:
        return "254" + digits
    return None


def vl_value() -> str:
    r = random.random()
    if r < 0.30:
        return "<50"
    if r < 0.50:
        return "LDL"
    if r < 0.60:
        return "TND"
    n = int(10 ** random.uniform(1.7, 5.7))
    if r < 0.65:
        return f"{n:,}"
    if r < 0.70:
        return f"{n} copies/ml"
    return str(n)


def hb_value() -> str:
    return "hemolysed" if random.random() < 0.03 else f"{random.gauss(11.5, 1.6):.1f}"


def rapid_value() -> str:
    return random.choices(["Negative", "Positive", "neg", "NEGATIVE"], weights=[88, 5, 4, 3])[0]


# ---------- builders ----------
def build_facilities(start: date) -> list[tuple]:
    rows, names, mfls, fid = [], set(), set(), 0
    for county, subs in COUNTIES.items():
        for _ in range(5):
            fid += 1
            level = random.choices([2, 3, 4, 5], weights=[45, 30, 18, 7])[0]
            sub = random.choice(subs)
            name = f"{sub} {LEVEL_SUFFIX[level]}"
            while name in names:
                name = f"{random.choice(PREFIXES)} {sub} {LEVEL_SUFFIX[level]}"
            names.add(name)
            mfl = str(random.randint(10000, 29999))
            while mfl in mfls:
                mfl = str(random.randint(10000, 29999))
            mfls.add(mfl)
            created = ts(start - timedelta(days=random.randint(365, 3650)))
            rows.append((fid, mfl, name, dirty_county(county), sub, level,
                         random.choice(OWNERSHIP), True, created, created))
    return rows


def build_patients(n: int, facility_ids: list[int], start: date, end: date):
    rows, meta = [], []
    for pid in range(1, n + 1):
        sex = "F" if random.random() < 0.6 else "M"
        age = int(random.triangular(0, 80, 27))
        dob = start - timedelta(days=age * 365 + random.randint(0, 364))
        created = ts(start - timedelta(days=random.randint(1, 720)))
        fac = random.choice(facility_ids)
        rows.append([pid, fac, f"UPI{random.randint(10**8, 10**9 - 1)}",
                     random.choice(FIRST_F if sex == "F" else FIRST_M), random.choice(LAST),
                     dirty_sex(sex), None if random.random() < 0.02 else dob,
                     phone(), created, created, None])
        meta.append({"pid": pid, "sex": sex, "dob": dob, "fac": fac, "from": start, "dup": False})

    # ~2% duplicate registrations: same person re-registered later, often at another facility
    next_id = n + 1
    for src in random.sample(rows[:n], int(n * 0.02)):
        dup = list(src)
        reg = rand_date(start, end)
        dup[0] = next_id
        dup[1] = random.choice(facility_ids)
        dup[3] = dup[3].upper() if random.random() < 0.5 else dup[3]
        dup[7] = phone()
        dup[8] = dup[9] = ts(reg)
        rows.append(dup)
        orig = meta[src[0] - 1]
        meta.append({"pid": next_id, "sex": orig["sex"], "dob": orig["dob"],
                     "fac": dup[1], "from": reg, "dup": True})
        next_id += 1
    return [tuple(r) for r in rows], meta


def build_visits(meta: list[dict], start: date, end: date):
    visits, labs = [], []

    def add_visit(m, d, vtype, anc_no=None):
        v = {"patient_id": m["pid"], "facility_id": m["fac"], "visit_date": d,
             "visit_type": vtype, "anc_visit_no": anc_no, "entered": d}
        visits.append(v)
        return v

    def add_lab(v, code, value):
        labs.append({"visit": v, "test_code": code, "result_value": value,
                     "result_date": v["visit_date"] + timedelta(days=random.randint(0, 14))})

    for m in meta:
        lo = max(start, m["from"])
        if lo > end:
            continue
        if m["dup"]:
            add_visit(m, rand_date(lo, end), "OPD")
            continue

        # OPD
        for _ in range(random.choices([0, 1, 2, 3], weights=[40, 35, 17, 8])[0]):
            add_visit(m, rand_date(lo, end), "OPD")

        # HIV care: visits every 28-90 days, viral load roughly every 6 months
        if random.random() < 0.12:
            d, last_vl = rand_date(lo, min(lo + timedelta(days=60), end)), None
            while d <= end:
                v = add_visit(m, d, "HIV_CARE")
                if (last_vl is None and random.random() < 0.5) or (last_vl and (d - last_vl).days >= 180):
                    add_lab(v, "VL", vl_value())
                    last_vl = d
                d += timedelta(days=random.randint(28, 90))

        # ANC (women 15-49), PNC after a completed 4-visit ANC course
        age = (start - m["dob"]).days // 365
        if m["sex"] == "F" and 15 <= age <= 49 and random.random() < 0.18:
            d, n_anc, completed = rand_date(lo, end), random.randint(1, 4), True
            for i in range(1, n_anc + 1):
                if d > end:
                    completed = False
                    break
                v = add_visit(m, d, "ANC", None if random.random() < 0.03 else i)
                if i == 1:
                    add_lab(v, "HB", hb_value())
                    add_lab(v, "HIV_RAPID", rapid_value())
                d += timedelta(days=random.randint(28, 42))
            pnc = d + timedelta(days=random.randint(30, 60))
            if completed and n_anc == 4 and pnc <= end:
                add_visit(m, pnc, "PNC")

    # data-entry defects
    for v in visits:
        r = random.random()
        if r < 0.02:
            v["visit_type"] = v["visit_type"].lower()
        elif r < 0.022:
            v["visit_date"] = end + timedelta(days=random.randint(30, 365))  # mistyped future date
    return visits, labs


def to_rows(visits: list[dict], labs: list[dict], end: date):
    for v in visits:
        v["created_at"] = ts(v["entered"])
    visits.sort(key=lambda v: v["created_at"])
    vrows = []
    for i, v in enumerate(visits, 1):
        v["visit_id"] = i
        vrows.append((i, v["patient_id"], v["facility_id"], v["visit_date"], v["visit_type"],
                      v["anc_visit_no"], v["created_at"], v["created_at"]))

    labs = [l for l in labs if l["result_date"] <= end]  # the rest have not been returned yet
    for l in labs:
        l["created_at"] = max(ts(l["result_date"]), l["visit"]["created_at"] + timedelta(hours=1))
    labs.sort(key=lambda l: l["created_at"])
    max_vid, lrows = len(visits), []
    for i, l in enumerate(labs, 1):
        r = random.random()
        vid = l["visit"]["visit_id"]
        if r < 0.01:
            vid = max_vid + random.randint(1000, 9999)  # orphan
        elif r < 0.015:
            vid = None
        lrows.append((i, vid, l["test_code"], l["result_value"], l["result_date"],
                      l["created_at"], l["created_at"]))
    return vrows, lrows


# ---------- load ----------
def load(data: list[list[tuple]], run_date: date) -> None:
    conn = psycopg2.connect(**DB)
    stats = {}
    try:
        with conn, conn.cursor() as cur:  # one transaction: all-or-nothing
            cur.execute("TRUNCATE emr.lab_results, emr.visits, emr.patients, emr.facilities RESTART IDENTITY CASCADE")
            cur.execute("TRUNCATE sim.generator_runs")
            for (table, pk, cols), rows in zip(TABLES, data):
                execute_values(cur, f"INSERT INTO emr.{table} ({cols}) VALUES %s", rows, page_size=1000)
                cur.execute(f"SELECT setval(pg_get_serial_sequence('emr.{table}', '{pk}'), "
                            f"(SELECT max({pk}) FROM emr.{table}))")
                stats[table] = len(rows)
                print(f"  emr.{table:<12} {len(rows):>6} rows")
            cur.execute("INSERT INTO sim.generator_runs (run_date, run_type, stats) VALUES (%s, 'init', %s)",
                        (run_date, json.dumps(stats)))
    finally:
        conn.close()


def cmd_init(args) -> None:
    random.seed(args.seed)
    end = args.end_date
    start = end - timedelta(days=args.days)
    facilities = build_facilities(start)
    patients, meta = build_patients(args.patients, [f[0] for f in facilities], start, end)
    visits, labs = build_visits(meta, start, end)
    vrows, lrows = to_rows(visits, labs, end)
    print(f"Seeding emr_source.emr  {start} -> {end}  (seed={args.seed})")
    load([facilities, patients, vrows, lrows], end)
    
# ---------- daily ----------
def p_due(days_since: int, lo: int, hi: int) -> float:
    """Daily probability that makes the gap roughly uniform between lo and hi days."""
    if days_since < lo:
        return 0.0
    if days_since >= hi:
        return 1.0
    return 1.0 / (hi - days_since + 1)


def cmd_daily(args) -> None:
    d: date = args.date
    random.seed(f"{args.seed}-{d.isoformat()}")  # same date + seed -> same data
    stats = {"new_patients": 0, "duplicates": 0, "visits": 0, "lab_results": 0, "updates": 0}
    conn = psycopg2.connect(**DB)
    try:
        with conn, conn.cursor() as cur:  # one transaction per simulated day
            # --- guard: dates must move forward ---
            cur.execute("SELECT max(run_date) FROM sim.generator_runs")
            last = cur.fetchone()[0]
            if last is None:
                raise SystemExit("No init run found. Run `init` first.")
            if d <= last:
                raise SystemExit(f"{d} already generated (last run {last}). Dates must move forward.")
            if (d - last).days > 1:
                print(f"WARNING: {(d - last).days - 1} day(s) skipped since {last}")

            # updates in this transaction get updated_at on the simulated clock
            cur.execute("SELECT set_config('emr.sim_now', %s, true)", (ts(d).isoformat(),))

            cur.execute("SELECT facility_id FROM emr.facilities WHERE is_active ORDER BY facility_id")
            fac_ids = [r[0] for r in cur.fetchall()]
            cur.execute("SELECT max(patient_id) FROM emr.patients")
            max_pid = cur.fetchone()[0]

            def add_visit(pid: int, fac: int, vtype: str, anc_no: int | None = None) -> None:
                vdate, r = d, random.random()
                if r < 0.02:
                    vtype = vtype.lower()
                elif r < 0.022:
                    vdate = d + timedelta(days=random.randint(30, 365))  # mistyped future date
                if anc_no is not None and random.random() < 0.03:
                    anc_no = None
                created = ts(d)
                cur.execute("""INSERT INTO emr.visits
                               (patient_id, facility_id, visit_date, visit_type, anc_visit_no, created_at, updated_at)
                               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                            (pid, fac, vdate, vtype, anc_no, created, created))
                stats["visits"] += 1

            # --- who is due today (computed before any inserts) ---
            cur.execute("""
                SELECT p.patient_id, p.facility_id, %(d)s - max(v.visit_date) AS days_since
                FROM emr.visits v
                JOIN emr.patients p ON p.patient_id = v.patient_id
                WHERE upper(v.visit_type) = 'HIV_CARE' AND v.visit_date <= %(d)s AND p.deleted_at IS NULL
                GROUP BY p.patient_id, p.facility_id
                ORDER BY p.patient_id""", {"d": d})
            hiv_due = cur.fetchall()

            cur.execute("""
                WITH anc AS (
                    SELECT patient_id, count(*) AS n_anc, max(visit_date) AS last_anc
                    FROM emr.visits
                    WHERE upper(visit_type) = 'ANC' AND visit_date BETWEEN %(d)s - 300 AND %(d)s
                    GROUP BY patient_id),
                pnc AS (
                    SELECT patient_id, max(visit_date) AS last_pnc
                    FROM emr.visits WHERE upper(visit_type) = 'PNC'
                    GROUP BY patient_id)
                SELECT a.patient_id, p.facility_id, a.n_anc, %(d)s - a.last_anc AS days_since
                FROM anc a
                JOIN emr.patients p ON p.patient_id = a.patient_id
                LEFT JOIN pnc ON pnc.patient_id = a.patient_id
                WHERE p.deleted_at IS NULL
                  AND (pnc.last_pnc IS NULL OR pnc.last_pnc < a.last_anc)
                ORDER BY a.patient_id""", {"d": d})
            anc_due = cur.fetchall()

            # --- new registrations (+ their first visit) ---
            for _ in range(random.randint(8, 20)):
                fac = random.choice(fac_ids)
                if random.random() < 0.02:  # duplicate registration of an existing person
                    cur.execute("SELECT upi, first_name, last_name, sex, date_of_birth "
                                "FROM emr.patients WHERE patient_id = %s", (random.randint(1, max_pid),))
                    src = cur.fetchone()
                    if src is None:
                        continue
                    upi, first, last_name, sex_raw, dob = src
                    if random.random() < 0.5:
                        first = first.upper()
                    true_sex, age = None, None
                    stats["duplicates"] += 1
                else:
                    true_sex = "F" if random.random() < 0.6 else "M"
                    age = int(random.triangular(0, 80, 27))
                    upi = f"UPI{random.randint(10**8, 10**9 - 1)}"
                    first = random.choice(FIRST_F if true_sex == "F" else FIRST_M)
                    last_name = random.choice(LAST)
                    sex_raw = dirty_sex(true_sex)
                    dob = None if random.random() < 0.02 else d - timedelta(days=age * 365 + random.randint(0, 364))
                created = ts(d)
                cur.execute("""INSERT INTO emr.patients
                               (facility_id, upi, first_name, last_name, sex, date_of_birth, phone, created_at, updated_at)
                               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING patient_id""",
                            (fac, upi, first, last_name, sex_raw, dob, phone(), created, created))
                pid = cur.fetchone()[0]
                stats["new_patients"] += 1
                if true_sex == "F" and 15 <= age <= 49 and random.random() < 0.18:
                    add_visit(pid, fac, "ANC", 1)
                elif true_sex and random.random() < 0.12:
                    add_visit(pid, fac, "HIV_CARE")
                else:
                    add_visit(pid, fac, "OPD")

            # --- OPD walk-ins ---
            for _ in range(random.randint(15, 30)):
                cur.execute("SELECT patient_id, facility_id FROM emr.patients "
                            "WHERE patient_id = %s AND deleted_at IS NULL", (random.randint(1, max_pid),))
                row = cur.fetchone()
                if row:
                    add_visit(row[0], row[1], "OPD")

            # --- scheduled follow-ups ---
            for pid, fac, days in hiv_due:
                if 28 <= days <= 90 and random.random() < p_due(days, 28, 90):
                    add_visit(pid, fac, "HIV_CARE")
            for pid, fac, n_anc, days in anc_due:
                if n_anc < 4 and 28 <= days <= 42 and random.random() < p_due(days, 28, 42):
                    add_visit(pid, fac, "ANC", n_anc + 1)
                elif n_anc >= 4 and 30 <= days <= 60 and random.random() < p_due(days, 30, 60):
                    add_visit(pid, fac, "PNC")

            # --- new pregnancies among existing women 15-49 ---
            for _ in range(random.randint(0, 3)):
                cur.execute("""
                    SELECT p.patient_id, p.facility_id FROM emr.patients p
                    WHERE p.patient_id = %(pid)s AND p.deleted_at IS NULL
                      AND upper(left(p.sex, 1)) = 'F'
                      AND p.date_of_birth BETWEEN %(d)s - 49 * 365 AND %(d)s - 15 * 365
                      AND NOT EXISTS (SELECT 1 FROM emr.visits v
                                      WHERE v.patient_id = p.patient_id
                                        AND upper(v.visit_type) = 'ANC' AND v.visit_date > %(d)s - 300)""",
                            {"pid": random.randint(1, max_pid), "d": d})
                row = cur.fetchone()
                if row:
                    add_visit(row[0], row[1], "ANC", 1)

            # --- lab results returning today (0-14 day turnaround, planned per visit) ---
            cur.execute("""
                SELECT visit_id, upper(visit_type), anc_visit_no, visit_date
                FROM emr.visits
                WHERE visit_date BETWEEN %(d)s - 14 AND %(d)s
                  AND upper(visit_type) IN ('ANC', 'HIV_CARE')
                ORDER BY visit_id""", {"d": d})
            for visit_id, vtype, anc_no, vdate in cur.fetchall():
                plan = random.Random(f"{args.seed}-lab-{visit_id}")
                if vtype == "ANC" and anc_no == 1:
                    tests = [("HB", hb_value), ("HIV_RAPID", rapid_value)]
                elif vtype == "HIV_CARE" and plan.random() < 0.35:
                    tests = [("VL", vl_value)]
                else:
                    continue
                for code, value_fn in tests:
                    if vdate + timedelta(days=plan.randint(0, 14)) != d:
                        continue
                    cur.execute("SELECT 1 FROM emr.lab_results WHERE visit_id = %s AND test_code = %s",
                                (visit_id, code))
                    if cur.fetchone():
                        continue
                    r = random.random()
                    vid = None if r < 0.005 else (visit_id + random.randint(10**6, 10**7) if r < 0.015 else visit_id)
                    created = ts(d)
                    cur.execute("""INSERT INTO emr.lab_results
                                   (visit_id, test_code, result_value, result_date, created_at, updated_at)
                                   VALUES (%s, %s, %s, %s, %s, %s)""",
                                (vid, code, value_fn(), d, created, created))
                    stats["lab_results"] += 1

            # --- edits to existing records (exercise updated_at-based extraction) ---
            for _ in range(random.randint(3, 8)):
                cur.execute("UPDATE emr.patients SET phone = %s WHERE patient_id = %s AND deleted_at IS NULL",
                            (phone(), random.randint(1, max_pid)))
                stats["updates"] += cur.rowcount
            for _ in range(random.randint(1, 3)):
                cur.execute("UPDATE emr.patients SET sex = upper(left(sex, 1)) "
                            "WHERE patient_id = %s AND sex IS NOT NULL", (random.randint(1, max_pid),))
                stats["updates"] += cur.rowcount
            if random.random() < 0.3:  # soft delete
                cur.execute("UPDATE emr.patients SET deleted_at = %s WHERE patient_id = %s AND deleted_at IS NULL",
                            (ts(d), random.randint(1, max_pid)))
                stats["updates"] += cur.rowcount
            if random.random() < 0.15:  # facility upgraded (feeds the snapshot module)
                cur.execute("UPDATE emr.facilities SET keph_level = keph_level + 1 "
                            "WHERE facility_id = %s AND keph_level < 5", (random.choice(fac_ids),))
                stats["updates"] += cur.rowcount
            if random.random() < 0.3:  # clerk corrects a mistyped future visit date
                cur.execute("""UPDATE emr.visits SET visit_date = created_at::date
                               WHERE visit_id = (SELECT min(visit_id) FROM emr.visits WHERE visit_date > %s)""", (d,))
                stats["updates"] += cur.rowcount

            cur.execute("INSERT INTO sim.generator_runs (run_date, run_type, stats) VALUES (%s, 'daily', %s)",
                        (d, json.dumps(stats)))
        print(f"{d}: {stats}")
    finally:
        conn.close()


def main() -> None:
    p = argparse.ArgumentParser(description="Simulated EMR data generator")
    sub = p.add_subparsers(dest="command", required=True)
    i = sub.add_parser("init", help="Truncate and seed historical data")
    i.add_argument("--days", type=int, default=180)
    i.add_argument("--patients", type=int, default=3000)
    i.add_argument("--end-date", type=date.fromisoformat, default=date.today() - timedelta(days=1))
    i.add_argument("--seed", type=int, default=42)
    i.set_defaults(func=cmd_init)
    
    dly = sub.add_parser("daily", help="Generate one simulated day of activity")
    dly.add_argument("--date", type=date.fromisoformat, required=True)
    dly.add_argument("--seed", type=int, default=42)
    dly.set_defaults(func=cmd_daily)
    
    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()