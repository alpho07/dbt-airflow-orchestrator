"""
Extract changed rows from emr_source.emr into dw.raw (windowed, idempotent).

Usage:
    python extract_emr.py --full --end 2026-10-10                 # initial load: everything before end
    python extract_emr.py --date 2026-10-10                       # one business day (Africa/Nairobi)
    python extract_emr.py --start 2026-10-10T06:00 --end 2026-10-10T12:00
"""
from __future__ import annotations

import argparse
import os
import uuid
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import psycopg2
from psycopg2.extras import execute_values

TZ = ZoneInfo("Africa/Nairobi")
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
CHUNK = 5000

SOURCE = {
    "host": os.getenv("EMR_PG_HOST", "localhost"),
    "port": int(os.getenv("EMR_PG_PORT", "5433")),
    "dbname": os.getenv("EMR_PG_DB", "emr_source"),
    "user": os.getenv("EMR_PG_USER", "dw_user"),
    "password": os.getenv("EMR_PG_PASSWORD"),
}
TARGET = {
    "host": os.getenv("DW_PG_HOST", "localhost"),
    "port": int(os.getenv("DW_PG_PORT", "5433")),
    "dbname": os.getenv("DW_PG_DB", "dw"),
    "user": os.getenv("DW_PG_USER", "dw_user"),
    "password": os.getenv("DW_PG_PASSWORD"),
}

TABLES: dict[str, list[str]] = {
    "facilities": ["facility_id", "mfl_code", "facility_name", "county", "sub_county",
                   "keph_level", "ownership", "is_active", "created_at", "updated_at"],
    "patients": ["patient_id", "facility_id", "upi", "first_name", "last_name", "sex",
                 "date_of_birth", "phone", "created_at", "updated_at", "deleted_at"],
    "visits": ["visit_id", "patient_id", "facility_id", "visit_date", "visit_type",
               "anc_visit_no", "created_at", "updated_at"],
    "lab_results": ["lab_result_id", "visit_id", "test_code", "result_value", "result_date",
                    "created_at", "updated_at"],
}


def parse_ts(s: str) -> datetime:
    """ISO date or datetime; naive values are interpreted as Africa/Nairobi."""
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=TZ)


def resolve_window(args) -> tuple[datetime, datetime]:
    if args.date:
        ws = datetime.combine(args.date, time.min, tzinfo=TZ)
        return ws, ws + timedelta(days=1)
    if not args.end:
        raise SystemExit("--end is required with --full or --start")
    ws = EPOCH if args.full else parse_ts(args.start)
    we = parse_ts(args.end)
    if ws >= we:
        raise SystemExit(f"Empty window: {ws} >= {we}")
    return ws, we


def run_table(table: str, cols: list[str], ws: datetime, we: datetime, batch_id: str, lcur) -> None:
    lcur.execute("""INSERT INTO raw._load_log (batch_id, source_table, window_start, window_end, status)
                    VALUES (%s, %s, %s, %s, 'running')""", (batch_id, table, ws, we))
    src = dw = None
    try:  # starts immediately after the 'running' row is written
        src = psycopg2.connect(**SOURCE)
        dw = psycopg2.connect(**TARGET)
        col_list = ", ".join(cols)
        with dw, dw.cursor() as wcur:  # one transaction: delete own slice + insert
            wcur.execute(f"DELETE FROM raw.emr_{table} WHERE _window_start = %s AND _window_end = %s", (ws, we))
            deleted, loaded = wcur.rowcount, 0
            with src, src.cursor(name=f"extract_{table}") as rcur:  # server-side cursor: streams rows
                rcur.itersize = CHUNK
                rcur.execute(f"""SELECT {col_list} FROM emr.{table}
                                 WHERE updated_at >= %s AND updated_at < %s
                                 ORDER BY updated_at""", (ws, we))
                while rows := rcur.fetchmany(CHUNK):
                    execute_values(
                        wcur,
                        f"INSERT INTO raw.emr_{table} ({col_list}, _window_start, _window_end, _batch_id) VALUES %s",
                        [row + (ws, we, batch_id) for row in rows],
                        page_size=1000,
                    )
                    loaded += len(rows)
        lcur.execute("""UPDATE raw._load_log
                        SET status = 'success', rows_deleted = %s, rows_loaded = %s, finished_at = now()
                        WHERE batch_id = %s AND source_table = %s""", (deleted, loaded, batch_id, table))
        print(f"  {table:<12} deleted={deleted:<6} loaded={loaded}")
    except Exception:
        lcur.execute("""UPDATE raw._load_log SET status = 'failed', finished_at = now()
                        WHERE batch_id = %s AND source_table = %s""", (batch_id, table))
        raise
    finally:
        for conn in (src, dw):
            if conn is not None:
                conn.close()


def main() -> None:
    p = argparse.ArgumentParser(description="EMR -> dw.raw windowed extract")
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--date", type=date.fromisoformat, help="business day in Africa/Nairobi")
    mode.add_argument("--full", action="store_true", help="from epoch to --end")
    mode.add_argument("--start", help="window start (ISO); requires --end")
    p.add_argument("--end", help="window end (ISO, exclusive)")
    p.add_argument("--tables", nargs="+", choices=list(TABLES), default=list(TABLES))
    args = p.parse_args()

    ws, we = resolve_window(args)
    batch_id = f"{datetime.now(TZ):%Y%m%dT%H%M%S}_{uuid.uuid4().hex[:8]}"
    print(f"batch {batch_id}  window [{ws.isoformat()}, {we.isoformat()})")

    logc = psycopg2.connect(**TARGET)
    logc.autocommit = True  # log rows survive a failed data transaction
    try:
        with logc.cursor() as lcur:
            for table in args.tables:
                run_table(table, TABLES[table], ws, we, batch_id, lcur)
    finally:
        logc.close()


if __name__ == "__main__":
    main()