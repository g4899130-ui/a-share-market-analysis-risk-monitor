"""Collect 20 A-share quotes in SQLite and analyze historical risk and returns.

Requires requests and pandas. Run `python main.py --help`.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from contextlib import closing
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import requests


ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "stock.db"
# 默认回补约 400 个自然日，通常覆盖约一年的交易日。
START_DATE = (date.today() - timedelta(days=400)).isoformat()
STOCKS = (
    "600519", "600036", "601398", "601318", "600276",
    "600900", "600030", "601012", "600309", "601888",
    "000001", "000333", "000858", "000651", "002415",
    "002594", "300750", "300059", "300760", "002475",
)
DAILY_URL = "http://push2his.eastmoney.com/api/qt/stock/kline/get"
DAILY_BACKUP_URL = "http://27.push2his.eastmoney.com/api/qt/stock/kline/get"
QUOTE_URL = "http://push2.eastmoney.com/api/qt/stock/get"
QUOTE_BACKUP_URL = "http://82.push2.eastmoney.com/api/qt/stock/get"


def secid(code: str) -> str:
    return ("1." if code.startswith("6") else "0.") + code


def get_json(url: str, params: dict[str, str]) -> dict:
    last_error = None
    for attempt in range(3):
        if attempt == 1 and url == DAILY_URL:
            target = DAILY_BACKUP_URL
        elif attempt == 1 and url == QUOTE_URL:
            target = QUOTE_BACKUP_URL
        else:
            target = url
        try:
            response = requests.get(target, params=params, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"}, timeout=15)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict) or payload.get("rc") != 0 or not isinstance(payload.get("data"), dict):
                raise ValueError("Unexpected API response structure or status")
            return payload["data"]
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            if attempt < 2:
                time.sleep((attempt + 1) * 2)
    raise RuntimeError(f"Request failed for {params['secid']}: {last_error}")


def fetch_daily(code: str, end_date: str, start_date: str | None = None) -> tuple[str, list[tuple]]:
    start_date = start_date or START_DATE
    data = get_json(DAILY_URL, {
        "secid": secid(code), "klt": "101", "fqt": "0",
        "beg": start_date.replace("-", ""), "end": end_date.replace("-", ""),
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57",
    })
    if data.get("code") != code or not isinstance(data.get("klines"), list):
        raise ValueError(f"Invalid daily data for {code}")
    records = [item.split(",") for item in data["klines"]]
    if any(len(item) < 7 for item in records):
        raise ValueError(f"Incomplete daily row for {code}")
    frame = pd.DataFrame([item[:7] for item in records], columns=["trade_date", "open", "close", "high", "low", "volume_lots", "amount_yuan"])
    if frame.empty:
        return str(data.get("name") or code), []
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="raise").dt.strftime("%Y-%m-%d")
    for column in ("open", "high", "low", "close", "volume_lots", "amount_yuan"):
        frame[column] = pd.to_numeric(frame[column], errors="raise")
    frame = frame.loc[frame["trade_date"].between(start_date, end_date)].copy()
    if ((frame[["open", "high", "low", "close"]] <= 0).any().any()
            or (frame["volume_lots"] < 0).any()
            or (frame["amount_yuan"] < 0).any()
            or (frame["low"] > frame[["open", "close"]].min(axis=1)).any()
            or (frame["high"] < frame[["open", "close"]].max(axis=1)).any()):
        raise ValueError(f"Invalid daily prices for {code}")
    frame.insert(0, "code", code)
    rows = [(row.code, row.trade_date, float(row.open), float(row.high), float(row.low),
             float(row.close), int(row.volume_lots), float(row.amount_yuan))
            for row in frame.itertuples(index=False)]
    return str(data.get("name") or code), rows


def fetch_quote(code: str) -> tuple:
    data = get_json(QUOTE_URL, {
        "secid": secid(code), "fields": "f43,f47,f48,f57,f58,f60,f86,f170",
        "invt": "2", "fltt": "2",
    })
    if data.get("f57") != code or not data.get("f58"):
        raise ValueError(f"Invalid quote identity for {code}")
    if not isinstance(data.get("f43"), (float, int)) or data["f43"] <= 0:
        raise ValueError(f"No usable quote price for {code}")
    stamp = data.get("f86")
    if not isinstance(stamp, (float, int)) or stamp <= 0:
        raise ValueError(f"No quote timestamp for {code}")
    quote_time = datetime.fromtimestamp(stamp).astimezone().isoformat(timespec="seconds")
    fetched_at = datetime.now().astimezone().isoformat(timespec="seconds")
    return (code, data["f58"], float(data["f43"]), data.get("f170"),
            data.get("f47"), data.get("f48"), quote_time, fetched_at)


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS stocks (
            code TEXT PRIMARY KEY, name TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS daily_bars (
            code TEXT NOT NULL REFERENCES stocks(code),
            trade_date TEXT NOT NULL,
            open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL,
            close REAL NOT NULL, volume_lots INTEGER NOT NULL,
            amount_yuan REAL NOT NULL,
            PRIMARY KEY (code, trade_date)
        );
        CREATE TABLE IF NOT EXISTS latest_quotes (
            code TEXT PRIMARY KEY REFERENCES stocks(code),
            name TEXT NOT NULL, price REAL NOT NULL,
            change_pct REAL, volume_lots INTEGER, amount_yuan REAL,
            quote_time TEXT NOT NULL, fetched_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS update_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT NOT NULL, finished_at TEXT,
            succeeded INTEGER NOT NULL DEFAULT 0,
            failed INTEGER NOT NULL DEFAULT 0,
            errors TEXT NOT NULL DEFAULT ''
        );
    """)
    return conn


def save_stock(conn: sqlite3.Connection, code: str, name: str, bars: list[tuple], quote: tuple) -> None:
    with conn:
        conn.execute("INSERT INTO stocks(code, name) VALUES (?, ?) ON CONFLICT(code) DO UPDATE SET name=excluded.name", (code, name))
        conn.executemany("""
            INSERT INTO daily_bars VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(code, trade_date) DO UPDATE SET
            open=excluded.open, high=excluded.high, low=excluded.low,
            close=excluded.close, volume_lots=excluded.volume_lots,
            amount_yuan=excluded.amount_yuan
        """, bars)
        conn.execute("""
            INSERT INTO latest_quotes VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(code) DO UPDATE SET
            name=excluded.name, price=excluded.price,
            change_pct=excluded.change_pct, volume_lots=excluded.volume_lots,
            amount_yuan=excluded.amount_yuan, quote_time=excluded.quote_time,
            fetched_at=excluded.fetched_at
        """, quote)


def update(conn: sqlite3.Connection, *, quotes_only: bool = False, start_date: str | None = None) -> tuple[int, int]:
    started = datetime.now().astimezone().isoformat(timespec="seconds")
    run_id = conn.execute("INSERT INTO update_runs(started_at) VALUES (?)", (started,)).lastrowid
    conn.commit()
    succeeded, errors = 0, []
    end_date = date.today().isoformat()
    for code in STOCKS:
        try:
            if quotes_only:
                name = conn.execute("SELECT name FROM stocks WHERE code=?", (code,)).fetchone()
                if name is None:
                    raise ValueError("Missing historical data; run update first")
                name, bars = name[0], []
            else:
                name, bars = fetch_daily(code, end_date, start_date)
                if not bars:
                    raise ValueError("No daily bars in requested period")
            quote = fetch_quote(code)
            save_stock(conn, code, name, bars, quote)
            succeeded += 1
            print(f"{code} {name}: {len(bars)} daily bars, quote {quote[6]}", flush=True)
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            message = f"{code}: {exc}"
            errors.append(message)
            print(f"ERROR {message}", file=sys.stderr, flush=True)
        time.sleep(0.2)
    finished = datetime.now().astimezone().isoformat(timespec="seconds")
    with conn:
        conn.execute("UPDATE update_runs SET finished_at=?, succeeded=?, failed=?, errors=? WHERE id=?",
                     (finished, succeeded, len(errors), "\n".join(errors), run_id))
    print(f"Completed: {succeeded} succeeded, {len(errors)} failed")
    return succeeded, len(errors)


def status(conn: sqlite3.Connection) -> None:
    for label, sql in (
        ("Stocks", "SELECT COUNT(*) FROM stocks"),
        ("Daily bars", "SELECT COUNT(*) FROM daily_bars"),
        ("Quotes", "SELECT COUNT(*) FROM latest_quotes"),
        ("Date range", "SELECT MIN(trade_date) || ' to ' || MAX(trade_date) FROM daily_bars"),
        ("Latest quote time", "SELECT MAX(quote_time) FROM latest_quotes"),
    ):
        print(f"{label}: {conn.execute(sql).fetchone()[0]}")
    row = conn.execute("SELECT started_at, finished_at, succeeded, failed FROM update_runs ORDER BY id DESC LIMIT 1").fetchone()
    if row:
        print(f"Last run: {row[0]} to {row[1]}, {row[2]} succeeded, {row[3]} failed")
    sample = pd.read_sql_query("SELECT code, name, price, quote_time FROM latest_quotes ORDER BY code LIMIT 5", conn)
    if not sample.empty:
        print("Latest quote sample:")
        print(sample.to_string(index=False))


def export(conn: sqlite3.Connection) -> None:
    for table in ("stocks", "daily_bars", "latest_quotes"):
        order = "code, trade_date" if table == "daily_bars" else "code"
        frame = pd.read_sql_query(f"SELECT * FROM {table} ORDER BY {order}", conn)
        path = DB_PATH.parent / f"{table}.csv"
        frame.to_csv(path, index=False, encoding="utf-8-sig")
        print(path)


def backfill(conn: sqlite3.Connection, start_date: str | None = None) -> tuple[int, int]:
    """只回补历史日线；行情快照接口不可用时仍可独立补齐日线。"""
    succeeded, errors = 0, []
    for code in STOCKS:
        try:
            name, bars = fetch_daily(code, date.today().isoformat(), start_date)
            if not bars:
                raise ValueError("No daily bars in requested period")
            with conn:
                conn.execute("INSERT INTO stocks(code, name) VALUES (?, ?) ON CONFLICT(code) DO UPDATE SET name=excluded.name", (code, name))
                conn.executemany("""
                    INSERT INTO daily_bars VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(code, trade_date) DO UPDATE SET
                    open=excluded.open, high=excluded.high, low=excluded.low,
                    close=excluded.close, volume_lots=excluded.volume_lots,
                    amount_yuan=excluded.amount_yuan
                """, bars)
            succeeded += 1
            print(f"{code} {name}: {len(bars)} daily bars", flush=True)
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            errors.append(f"{code}: {exc}")
            print(f"ERROR {errors[-1]}", file=sys.stderr, flush=True)
        time.sleep(0.2)
    print(f"Backfill completed: {succeeded} succeeded, {len(errors)} failed")
    return succeeded, len(errors)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("update", "quotes", "watch", "status", "export", "backfill", "analyze"))
    parser.add_argument("--interval", type=int, default=60, help="Seconds between watch runs (minimum 30)")
    parser.add_argument("--start", help="Analysis start date or historical fetch start, YYYY-MM-DD")
    parser.add_argument("--end", help="Analysis end date, YYYY-MM-DD")
    parser.add_argument("--codes", nargs="*", help="Stock codes for analyze; default: all in database")
    args = parser.parse_args()
    if args.command == "watch" and args.interval < 30:
        parser.error("--interval must be at least 30")
    with closing(connect()) as conn:
        if args.command == "update":
            _, failed = update(conn, start_date=args.start)
            return 1 if failed else 0
        if args.command == "backfill":
            _, failed = backfill(conn, args.start)
            return 1 if failed else 0
        if args.command == "analyze":
            from analysis import run_analysis
            run_analysis(conn, ROOT / "results", start=args.start, end=args.end, codes=args.codes)
            return 0
        if args.command == "quotes":
            _, failed = update(conn, quotes_only=True)
            return 1 if failed else 0
        if args.command == "status":
            status(conn)
        elif args.command == "export":
            export(conn)
        else:
            try:
                last_daily_date = None
                next_daily_attempt = 0.0
                while True:
                    today = date.today()
                    if today != last_daily_date and time.monotonic() >= next_daily_attempt:
                        _, failed = update(conn)
                        if not failed:
                            last_daily_date = today
                        else:
                            next_daily_attempt = time.monotonic() + 3600
                    else:
                        update(conn, quotes_only=True)
                    time.sleep(args.interval)
            except KeyboardInterrupt:
                print("Stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
