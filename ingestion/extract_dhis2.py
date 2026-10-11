"""
Extract DHIS2 ANC data elements (district x month) into dw.raw.dhis2_analytics.

Rolling window: each run re-pulls the N complete months before --as-of,
because DHIS2 values for recent months change as late reports arrive.
Idempotent per (as_of, request_key).

Env: DHIS2_URL, DHIS2_USER, DHIS2_PASSWORD, DW_PG_* (optional, defaults as extract_emr.py)

Usage:
    python extract_dhis2.py --as-of 2026-10-11 [--months 12]
"""
from __future__ import annotations

import argparse
import os
import uuid
from datetime import date, datetime
from zoneinfo import ZoneInfo

import psycopg2
import requests
from psycopg2.extras import Json
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

TZ = ZoneInfo("Africa/Nairobi")
DX = {  # reference by UID, never by name
    "fbfJHSPpUQD": "ANC 1st visit",
    "cYeuwXTCPkU": "ANC 2nd visit",
    "Jtf34kNZhzP": "ANC 3rd visit",
    "hfdmMSPBgLG": "ANC 4th or more visits",
}
ROOT_OU = "ImspTQPwCqd"
OU_LEVEL = 2
EXPECTED_HEADERS = ["dx", "ou", "pe", "value"]

TARGET = {
    "host": os.getenv("DW_PG_HOST", "localhost"),
    "port": int(os.getenv("DW_PG_PORT", "5433")),
    "dbname": os.getenv("DW_PG_DB", "dw"),
    "user": os.getenv("DW_PG_USER", "dw_user"),
    "password": os.getenv("DW_PG_PASSWORD"),
}


def env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise SystemExit(f"Missing environment variable {name}")
    return value


def month_periods(as_of: date, months: int) -> list[str]:
    """N complete months before as_of's month, as DHIS2 periods (YYYYMM), oldest first."""
    y, m, out = as_of.year, as_of.month, []
    for _ in range(months):
        m -= 1
        if m == 0:
            y, m = y - 1, 12
        out.append(f"{y}{m:02d}")
    return sorted(out)


def http_session(user: str, password: str) -> requests.Session:
    s = requests.Session()
    s.auth = (user, password)
    retry = Retry(total=5, backoff_factor=2, status_forcelist=[429, 500, 502, 503, 504],
                  allowed_methods=["GET"])
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.mount("http://", HTTPAdapter(max_retries=retry))
    return s


def fetch(session: requests.Session, base_url: str, periods: list[str]) -> tuple[str, dict]:
    params = [
        ("dimension", "dx:" + ";".join(DX)),
        ("dimension", f"ou:{ROOT_OU};LEVEL-{OU_LEVEL}"),
        ("dimension", "pe:" + ";".join(periods)),
    ]
    resp = session.get(f"{base_url}/api/analytics.json", params=params, timeout=(10, 120))
    resp.raise_for_status()
    payload = resp.json()
    headers = [h["name"] for h in payload.get("headers", [])]
    if headers != EXPECTED_HEADERS:
        raise RuntimeError(f"Schema drift: expected {EXPECTED_HEADERS}, got {headers}")
    return resp.url, payload


def main() -> None:
    p = argparse.ArgumentParser(description="DHIS2 analytics -> dw.raw (rolling window)")
    p.add_argument("--as-of", type=date.fromisoformat, default=datetime.now(TZ).date())
    p.add_argument("--months", type=int, default=12)
    args = p.parse_args()

    base_url = env("DHIS2_URL").rstrip("/")
    periods = month_periods(args.as_of, args.months)
    request_key = f"analytics|dx={','.join(sorted(DX))}|ou={ROOT_OU};LEVEL-{OU_LEVEL}|months={args.months}"
    ws = datetime(int(periods[0][:4]), int(periods[0][4:]), 1, tzinfo=TZ)
    we = datetime(args.as_of.year, args.as_of.month, 1, tzinfo=TZ)
    batch_id = f"{datetime.now(TZ):%Y%m%dT%H%M%S}_{uuid.uuid4().hex[:8]}"
    print(f"batch {batch_id}  as_of {args.as_of}  periods {periods[0]}..{periods[-1]}")

    logc = psycopg2.connect(**TARGET)
    logc.autocommit = True
    lcur = logc.cursor()
    lcur.execute("""INSERT INTO raw._load_log (batch_id, source_table, window_start, window_end, status)
                    VALUES (%s, 'dhis2_analytics', %s, %s, 'running')""", (batch_id, ws, we))
    dw = None
    try:
        session = http_session(env("DHIS2_USER"), env("DHIS2_PASSWORD"))
        url, payload = fetch(session, base_url, periods)
        n_rows = len(payload.get("rows", []))
        dw = psycopg2.connect(**TARGET)
        with dw, dw.cursor() as cur:
            cur.execute("DELETE FROM raw.dhis2_analytics WHERE as_of = %s AND request_key = %s",
                        (args.as_of, request_key))
            replaced = cur.rowcount
            cur.execute("""INSERT INTO raw.dhis2_analytics
                           (as_of, request_key, request_url, periods, payload, row_count, _batch_id)
                           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                        (args.as_of, request_key, url, periods, Json(payload), n_rows, batch_id))
        lcur.execute("""UPDATE raw._load_log
                        SET status = 'success', rows_deleted = %s, rows_loaded = %s, finished_at = now()
                        WHERE batch_id = %s AND source_table = 'dhis2_analytics'""",
                     (replaced, n_rows, batch_id))
        print(f"  dhis2_analytics  snapshots_replaced={replaced}  rows={n_rows}")
    except Exception:
        lcur.execute("""UPDATE raw._load_log SET status = 'failed', finished_at = now()
                        WHERE batch_id = %s AND source_table = 'dhis2_analytics'""", (batch_id,))
        raise
    finally:
        if dw is not None:
            dw.close()
        lcur.close()
        logc.close()


if __name__ == "__main__":
    main()