#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Taiwan Lotto 6/49 iron-law prediction and cloud mobile site builder.

This system follows the 539 iron-law workflow:
1. Build and verify the full official historical database first.
2. Only then calculate predictions, settlement, reports, and mobile cloud output.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import io
import json
import logging
import math
import shutil
import sqlite3
import ssl
import sys
import time
import urllib.parse
import urllib.request
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, time as clock_time, timedelta
from itertools import combinations
from pathlib import Path
from statistics import mean
from zoneinfo import ZoneInfo


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
REPORT_DIR = BASE_DIR / "reports"
MOBILE_DIR = BASE_DIR / "mobile_cloud"
PAGES_DIR = BASE_DIR / "docs"
BACKUP_DIR = BASE_DIR / "backups"
LOG_DIR = BASE_DIR / "logs"

DB_PATH = DATA_DIR / "lotto649.sqlite"
CSV_PATH = DATA_DIR / "lotto649.csv"
ANALYSIS_JSON = REPORT_DIR / "latest_analysis.json"
HEALTH_JSON = REPORT_DIR / "system_health.json"
BATTLE_MD = REPORT_DIR / "latest_battle_report.md"
BATTLE_HTML = REPORT_DIR / "latest_battle_report.html"
ENHANCED_BATTLE_HTML = REPORT_DIR / "大樂透最新強化戰報.html"
HISTORY_JSON = REPORT_DIR / "prediction_history.json"
SELF_TEST_JSON = REPORT_DIR / "self_test_report.json"

API_BASE = "https://api.taiwanlottery.com/TLCAPIWeB"
DOWNLOAD_API = f"{API_BASE}/Lottery/ResultDownload"
LATEST_API = f"{API_BASE}/Lottery/LatestResult"
HISTORY_API = f"{API_BASE}/Lottery/Lotto649Result"

START_GREGORIAN_YEAR = 2007
NUMBER_MAX = 49
MAIN_DRAW_SIZE = 6
TAIPEI_TZ = ZoneInfo("Asia/Taipei")

DEFAULT_MODEL_WEIGHTS = {
    "heat_short": 1.12,
    "heat_mid": 1.08,
    "heat_long": 0.78,
    "bayes_global": 0.96,
    "ewma_fast": 1.18,
    "ewma_slow": 0.94,
    "omission": 0.92,
    "gap_hazard": 1.05,
    "pair": 1.02,
    "markov_transition": 1.10,
    "tail_zone": 0.72,
    "chi_square_balance": 0.70,
    "repeat_neighbor": 0.52,
    "mirror": 0.34,
    "special_bridge": 0.44,
}

SPECIAL_MODEL_WEIGHTS = {
    "special_short": 1.12,
    "special_mid": 1.02,
    "special_long": 0.78,
    "special_bayes": 0.96,
    "special_ewma": 1.10,
    "special_omission": 0.92,
    "special_gap_hazard": 0.92,
    "main_bridge": 0.46,
}


def taipei_now() -> datetime:
    return datetime.now(TAIPEI_TZ).replace(tzinfo=None)


def setup_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(LOG_DIR / "lotto649_update.log", encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def roc_year_now() -> int:
    return taipei_now().year - 1911


def expected_latest_draw_date(now: datetime | None = None) -> str:
    now = now or taipei_now()
    candidate = now.date()
    if now.time() < clock_time(21, 0):
        candidate -= timedelta(days=1)
    while candidate.weekday() not in {1, 4}:  # Tuesday, Friday
        candidate -= timedelta(days=1)
    return candidate.isoformat()


def next_draw_date(date_text: str) -> str:
    candidate = datetime.strptime(date_text, "%Y-%m-%d").date() + timedelta(days=1)
    while candidate.weekday() not in {1, 4}:
        candidate += timedelta(days=1)
    return candidate.isoformat()


def data_freshness(latest_date: str, now: datetime | None = None) -> dict:
    now = now or taipei_now()
    expected = expected_latest_draw_date(now)
    latest = datetime.strptime(latest_date, "%Y-%m-%d").date()
    expected_dt = datetime.strptime(expected, "%Y-%m-%d").date()
    lag_days = max((expected_dt - latest).days, 0)
    return {
        "status": "fresh" if latest >= expected_dt else "stale",
        "latest_date": latest_date,
        "expected_latest_date": expected,
        "lag_days": lag_days,
        "checked_at": now.isoformat(timespec="seconds"),
    }


def http_get_bytes(url: str, retries: int = 3, retry_delay: int = 2) -> bytes:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 lotto649-ironlaw/1.0",
            "Accept": "*/*",
        },
    )
    context = ssl._create_unverified_context()
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=60, context=context) as response:
                data = response.read()
                if not data:
                    raise RuntimeError("empty response")
                return data
        except Exception as exc:  # pragma: no cover - network failures are environmental
            last_error = exc
            logging.warning("下載失敗，第 %s/%s 次：%s", attempt, retries, url)
            if attempt < retries:
                time.sleep(retry_delay * attempt)
    raise RuntimeError(f"下載失敗：{url} ({last_error})")


def http_get_json(url: str, params: dict | None = None) -> dict:
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    raw = http_get_bytes(url)
    return json.loads(raw.decode("utf-8-sig"))


def to_int(value) -> int | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if text == "":
        return None
    return int(float(text))


def normalize_date(value) -> str:
    text = str(value).strip()
    if "T" in text:
        text = text.split("T", 1)[0].replace("-", "/")
    return datetime.strptime(text, "%Y/%m/%d").strftime("%Y-%m-%d")


def fmt_numbers(numbers) -> str:
    return " ".join(f"{int(n):02d}" for n in numbers)


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS draws_lotto649 (
            period INTEGER PRIMARY KEY,
            draw_date TEXT NOT NULL,
            n1 INTEGER NOT NULL,
            n2 INTEGER NOT NULL,
            n3 INTEGER NOT NULL,
            n4 INTEGER NOT NULL,
            n5 INTEGER NOT NULL,
            n6 INTEGER NOT NULL,
            special INTEGER NOT NULL,
            draw_order TEXT,
            sales_amount INTEGER,
            sales_count INTEGER,
            prize_total INTEGER,
            jackpot_winners INTEGER,
            second_winners INTEGER,
            third_winners INTEGER,
            fourth_winners INTEGER,
            fifth_winners INTEGER,
            sixth_winners INTEGER,
            seventh_winners INTEGER,
            normal_winners INTEGER,
            source TEXT NOT NULL,
            fetched_at TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_draws_lotto649_date ON draws_lotto649(draw_date)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS update_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_type TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            status TEXT NOT NULL,
            message TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS predictions_lotto649 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            based_on_period INTEGER NOT NULL UNIQUE,
            based_on_date TEXT NOT NULL,
            target_period INTEGER,
            target_date TEXT,
            candidates_json TEXT NOT NULL,
            special_candidates_json TEXT NOT NULL,
            suggested_sets_json TEXT NOT NULL,
            strong_packs_json TEXT,
            model_weights_json TEXT NOT NULL,
            backtest_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            settled_at TEXT,
            actual_period INTEGER,
            actual_date TEXT,
            actual_numbers_json TEXT,
            actual_special INTEGER,
            top6_hits INTEGER,
            top12_hits INTEGER,
            top18_hits INTEGER,
            special_top1_hit INTEGER,
            special_top3_hit INTEGER,
            set_hits_json TEXT,
            strong_pack_hits_json TEXT,
            status TEXT NOT NULL DEFAULT 'pending'
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_predictions_lotto649_status ON predictions_lotto649(status)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS prediction_snapshots_lotto649 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            based_on_period INTEGER NOT NULL,
            based_on_date TEXT NOT NULL,
            target_period INTEGER,
            target_date TEXT,
            candidates_json TEXT NOT NULL,
            special_candidates_json TEXT NOT NULL,
            suggested_sets_json TEXT NOT NULL,
            strong_packs_json TEXT,
            model_weights_json TEXT NOT NULL,
            backtest_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            snapshot_reason TEXT NOT NULL
        )
        """
    )
    conn.commit()


def start_run(conn: sqlite3.Connection, run_type: str) -> int:
    cursor = conn.execute(
        "INSERT INTO update_runs (run_type, started_at, status) VALUES (?, ?, ?)",
        (run_type, taipei_now().isoformat(timespec="seconds"), "running"),
    )
    conn.commit()
    return int(cursor.lastrowid)


def finish_run(conn: sqlite3.Connection, run_id: int, status: str, message: str = "") -> None:
    conn.execute(
        "UPDATE update_runs SET finished_at=?, status=?, message=? WHERE id=?",
        (taipei_now().isoformat(timespec="seconds"), status, message[:1000], run_id),
    )
    conn.commit()


def backup_database() -> Path | None:
    if not DB_PATH.exists():
        return None
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = taipei_now().strftime("%Y%m%d_%H%M%S")
    backup_path = BACKUP_DIR / f"lotto649_{stamp}.sqlite"
    shutil.copy2(DB_PATH, backup_path)
    backups = sorted(BACKUP_DIR.glob("lotto649_*.sqlite"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old_backup in backups[10:]:
        old_backup.unlink()
    return backup_path


def validate_draw_row(row: dict) -> None:
    nums = [row[f"n{i}"] for i in range(1, 7)]
    special = row["special"]
    if row["period"] is None:
        raise ValueError("期別不可為空")
    if len(set(nums)) != 6:
        raise ValueError(f"期別 {row['period']} 本號重複：{nums}")
    if any(n < 1 or n > NUMBER_MAX for n in nums):
        raise ValueError(f"期別 {row['period']} 本號超出 1-{NUMBER_MAX}：{nums}")
    if special < 1 or special > NUMBER_MAX:
        raise ValueError(f"期別 {row['period']} 特別號超出 1-{NUMBER_MAX}：{special}")
    if special in nums:
        raise ValueError(f"期別 {row['period']} 特別號與本號重複：{nums} + {special}")


def upsert_draw(conn: sqlite3.Connection, row: dict) -> None:
    validate_draw_row(row)
    conn.execute(
        """
        INSERT INTO draws_lotto649 (
            period, draw_date, n1, n2, n3, n4, n5, n6, special, draw_order,
            sales_amount, sales_count, prize_total,
            jackpot_winners, second_winners, third_winners, fourth_winners,
            fifth_winners, sixth_winners, seventh_winners, normal_winners,
            source, fetched_at
        )
        VALUES (
            :period, :draw_date, :n1, :n2, :n3, :n4, :n5, :n6, :special, :draw_order,
            :sales_amount, :sales_count, :prize_total,
            :jackpot_winners, :second_winners, :third_winners, :fourth_winners,
            :fifth_winners, :sixth_winners, :seventh_winners, :normal_winners,
            :source, :fetched_at
        )
        ON CONFLICT(period) DO UPDATE SET
            draw_date=excluded.draw_date,
            n1=excluded.n1,
            n2=excluded.n2,
            n3=excluded.n3,
            n4=excluded.n4,
            n5=excluded.n5,
            n6=excluded.n6,
            special=excluded.special,
            draw_order=COALESCE(excluded.draw_order, draws_lotto649.draw_order),
            sales_amount=COALESCE(excluded.sales_amount, draws_lotto649.sales_amount),
            sales_count=COALESCE(excluded.sales_count, draws_lotto649.sales_count),
            prize_total=COALESCE(excluded.prize_total, draws_lotto649.prize_total),
            jackpot_winners=COALESCE(excluded.jackpot_winners, draws_lotto649.jackpot_winners),
            second_winners=COALESCE(excluded.second_winners, draws_lotto649.second_winners),
            third_winners=COALESCE(excluded.third_winners, draws_lotto649.third_winners),
            fourth_winners=COALESCE(excluded.fourth_winners, draws_lotto649.fourth_winners),
            fifth_winners=COALESCE(excluded.fifth_winners, draws_lotto649.fifth_winners),
            sixth_winners=COALESCE(excluded.sixth_winners, draws_lotto649.sixth_winners),
            seventh_winners=COALESCE(excluded.seventh_winners, draws_lotto649.seventh_winners),
            normal_winners=COALESCE(excluded.normal_winners, draws_lotto649.normal_winners),
            source=excluded.source,
            fetched_at=excluded.fetched_at
        """,
        row,
    )


def fetch_year_zip_url(gregorian_year: int) -> str:
    payload = http_get_json(DOWNLOAD_API, {"year": gregorian_year})
    if payload.get("rtCode") != 0 or not (payload.get("content") or {}).get("path"):
        raise RuntimeError(f"年度 {gregorian_year} 下載 API 回傳異常: {payload}")
    return payload["content"]["path"]


def parse_csv_rows(zf: zipfile.ZipFile, filename: str) -> list[list[str]]:
    text = zf.read(filename).decode("utf-8-sig", errors="replace")
    return list(csv.reader(io.StringIO(text)))


def row_weekday(row: list[str]) -> int | None:
    try:
        return datetime.strptime(row[2].strip(), "%Y/%m/%d").weekday()
    except Exception:
        return None


def numeric_fields(row: list[str], start: int, count: int) -> list[int] | None:
    if len(row) <= start + count - 1:
        return None
    nums = [to_int(value) for value in row[start : start + count]]
    if any(value is None for value in nums):
        return None
    return [int(value) for value in nums]


def is_regular_lotto649_rows(rows: list[list[str]]) -> bool:
    total_rows = max(len(rows) - 1, 0)
    if total_rows < 40 or total_rows > 220:
        return False
    data_rows = [
        row
        for row in rows[1:60]
        if 13 <= len(row) <= 18 and to_int(row[1]) and row[2].strip()
    ]
    if len(data_rows) < 3:
        return False
    weekday_score = sum(1 for row in data_rows if row_weekday(row) in {1, 4}) / len(data_rows)
    parsed = []
    for row in data_rows:
        nums = numeric_fields(row, 6, 7)
        if not nums:
            return False
        main, special = nums[:6], nums[6]
        if len(set(main)) != 6 or special in main:
            return False
        if any(n < 1 or n > NUMBER_MAX for n in nums):
            return False
        parsed.extend(main)
    has_high_lotto_number = max(parsed) > 38
    name_hint = any("大樂透" in (row[0] or "") for row in data_rows[:5])
    return (name_hint or weekday_score >= 0.50) and has_high_lotto_number


def find_lotto649_csv(zf: zipfile.ZipFile) -> str:
    candidates = []
    for info in zf.infolist():
        if info.is_dir() or not info.filename.lower().endswith(".csv"):
            continue
        rows = parse_csv_rows(zf, info.filename)
        if is_regular_lotto649_rows(rows):
            candidates.append((len(rows), info.filename))
    if not candidates:
        raise RuntimeError("年度壓縮檔找不到大樂透 CSV")
    candidates.sort(reverse=True)
    return candidates[0][1]


def row_from_csv_item(item: list[str], source: str, fetched_at: str) -> dict | None:
    nums = numeric_fields(item, 6, 7)
    if not nums:
        return None
    main, special = nums[:6], nums[6]
    row = {
        "period": to_int(item[1]),
        "draw_date": normalize_date(item[2]),
        "n1": main[0],
        "n2": main[1],
        "n3": main[2],
        "n4": main[3],
        "n5": main[4],
        "n6": main[5],
        "special": special,
        "draw_order": None,
        "sales_amount": to_int(item[3]),
        "sales_count": to_int(item[4]),
        "prize_total": to_int(item[5]),
        "jackpot_winners": None,
        "second_winners": None,
        "third_winners": None,
        "fourth_winners": None,
        "fifth_winners": None,
        "sixth_winners": None,
        "seventh_winners": None,
        "normal_winners": None,
        "source": source,
        "fetched_at": fetched_at,
    }
    return row


def import_year(conn: sqlite3.Connection, gregorian_year: int) -> int:
    zip_url = fetch_year_zip_url(gregorian_year)
    zip_bytes = http_get_bytes(zip_url)
    imported = 0
    fetched_at = taipei_now().isoformat(timespec="seconds")
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        filename = find_lotto649_csv(zf)
        rows = parse_csv_rows(zf, filename)
        for item in rows[1:]:
            if len(item) < 13 or not to_int(item[1]):
                continue
            row = row_from_csv_item(item, f"taiwanlottery_result_download_{gregorian_year}", fetched_at)
            if not row:
                continue
            upsert_draw(conn, row)
            imported += 1
    conn.commit()
    return imported


def import_all_years(conn: sqlite3.Connection, start_year: int = START_GREGORIAN_YEAR, end_year: int | None = None) -> int:
    end_year = end_year or taipei_now().year
    total = 0
    for year in range(start_year, end_year + 1):
        try:
            count = import_year(conn, year)
            total += count
            logging.info("已匯入 %s 年大樂透：%s 筆", year, count)
            time.sleep(0.15)
        except Exception as exc:
            logging.error("%s 年匯入失敗：%s", year, exc)
    return total


def winner_count(assign: dict | None) -> int | None:
    return to_int((assign or {}).get("winnerCount"))


def row_from_api_result(result: dict, source: str) -> dict:
    nums = [int(n) for n in result["drawNumberSize"][:7]]
    main, special = nums[:6], nums[6]
    return {
        "period": to_int(result["period"]),
        "draw_date": normalize_date(result["lotteryDate"]),
        "n1": main[0],
        "n2": main[1],
        "n3": main[2],
        "n4": main[3],
        "n5": main[4],
        "n6": main[5],
        "special": special,
        "draw_order": ",".join(str(n).zfill(2) for n in result.get("drawNumberAppear", [])[:7]),
        "sales_amount": to_int(result.get("sellAmount")),
        "sales_count": None,
        "prize_total": to_int(result.get("totalAmount")),
        "jackpot_winners": winner_count(result.get("jackpotAssign")),
        "second_winners": winner_count(result.get("secondAssign")),
        "third_winners": winner_count(result.get("thirdAssign")),
        "fourth_winners": winner_count(result.get("fourthAssign")),
        "fifth_winners": winner_count(result.get("fifthAssign")),
        "sixth_winners": winner_count(result.get("sixthAssign")),
        "seventh_winners": winner_count(result.get("seventhAssign")),
        "normal_winners": winner_count(result.get("normalAssign")),
        "source": source,
        "fetched_at": taipei_now().isoformat(timespec="seconds"),
    }


def month_strings(count: int = 3) -> list[str]:
    today = taipei_now()
    year, month = today.year, today.month
    months = []
    for _ in range(count):
        months.append(f"{year:04d}-{month:02d}")
        month -= 1
        if month == 0:
            month = 12
            year -= 1
    return months


def import_month(conn: sqlite3.Connection, month: str) -> int:
    payload = http_get_json(
        HISTORY_API,
        {
            "month": month,
            "endMonth": month,
            "pageNum": 1,
            "pageSize": 100,
        },
    )
    content = payload.get("content") or {}
    results = content.get("lotto649Res") or []
    for result in results:
        upsert_draw(conn, row_from_api_result(result, f"taiwanlottery_lotto649_result_{month}"))
    conn.commit()
    logging.info("已補齊 %s 月查詢 API：%s 筆", month, len(results))
    return len(results)


def import_recent_months(conn: sqlite3.Connection, count: int = 3) -> int:
    return sum(import_month(conn, month) for month in month_strings(count))


def update_latest(conn: sqlite3.Connection) -> dict:
    payload = http_get_json(LATEST_API)
    content = payload.get("content") or {}
    latest = content.get("lotto649Result")
    if latest:
        row = row_from_api_result(latest, "taiwanlottery_latest_result")
        upsert_draw(conn, row)
        conn.commit()
    import_recent_months(conn, 3)
    latest_row = latest_draw(conn)
    logging.info("最新大樂透資料：%s %s", latest_row["period"], latest_row["draw_date"])
    return latest_row


def export_csv(conn: sqlite3.Connection) -> int:
    rows = conn.execute(
        """
        SELECT period, draw_date, n1, n2, n3, n4, n5, n6, special, draw_order,
               sales_amount, sales_count, prize_total,
               jackpot_winners, second_winners, third_winners, fourth_winners,
               fifth_winners, sixth_winners, seventh_winners, normal_winners,
               source, fetched_at
        FROM draws_lotto649
        ORDER BY period
        """
    ).fetchall()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with CSV_PATH.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
                "period",
                "draw_date",
                "n1",
                "n2",
                "n3",
                "n4",
                "n5",
                "n6",
                "special",
                "draw_order",
                "sales_amount",
                "sales_count",
                "prize_total",
                "jackpot_winners",
                "second_winners",
                "third_winners",
                "fourth_winners",
                "fifth_winners",
                "sixth_winners",
                "seventh_winners",
                "normal_winners",
                "source",
                "fetched_at",
            ]
        )
        writer.writerows(rows)
    return len(rows)


def load_draws(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """
        SELECT period, draw_date, n1, n2, n3, n4, n5, n6, special,
               sales_amount, sales_count, prize_total, source
        FROM draws_lotto649
        ORDER BY period
        """
    ).fetchall()
    return [
        {
            "period": row[0],
            "draw_date": row[1],
            "numbers": [row[2], row[3], row[4], row[5], row[6], row[7]],
            "special": row[8],
            "sales_amount": row[9],
            "sales_count": row[10],
            "prize_total": row[11],
            "source": row[12],
        }
        for row in rows
    ]


def latest_draw(conn: sqlite3.Connection) -> dict:
    draws = load_draws(conn)
    if not draws:
        raise RuntimeError("資料庫尚無大樂透資料")
    return draws[-1]


def history_health(conn: sqlite3.Connection) -> dict:
    draws = load_draws(conn)
    if not draws:
        return {"status": "empty", "draw_count": 0}
    periods = [draw["period"] for draw in draws]
    duplicate_dates = [
        date for date, count in Counter(draw["draw_date"] for draw in draws).items() if count > 1
    ]
    invalid = []
    for draw in draws:
        try:
            validate_draw_row(
                {
                    "period": draw["period"],
                    "n1": draw["numbers"][0],
                    "n2": draw["numbers"][1],
                    "n3": draw["numbers"][2],
                    "n4": draw["numbers"][3],
                    "n5": draw["numbers"][4],
                    "n6": draw["numbers"][5],
                    "special": draw["special"],
                }
            )
        except Exception as exc:
            invalid.append({"period": draw["period"], "message": str(exc)})
    latest = draws[-1]
    freshness = data_freshness(latest["draw_date"])
    status = "ready" if not invalid and freshness["status"] == "fresh" else "review"
    return {
        "status": status,
        "draw_count": len(draws),
        "first_period": periods[0],
        "first_date": draws[0]["draw_date"],
        "latest_period": latest["period"],
        "latest_date": latest["draw_date"],
        "official_download_range_note": "台灣彩券官方年度下載與月查詢 API 可取得範圍",
        "duplicate_dates": duplicate_dates[:10],
        "invalid_rows": invalid[:20],
        "freshness": freshness,
        "generated_at": taipei_now().isoformat(timespec="seconds"),
    }


def normalize_map(values: dict[int, float]) -> dict[int, float]:
    if not values:
        return {n: 0.0 for n in range(1, NUMBER_MAX + 1)}
    max_v = max(values.values())
    min_v = min(values.values())
    if max_v == min_v:
        return {n: 0.5 for n in range(1, NUMBER_MAX + 1)}
    return {n: (values.get(n, min_v) - min_v) / (max_v - min_v) for n in range(1, NUMBER_MAX + 1)}


def rank_values(values: dict[int, float]) -> list[int]:
    return sorted(range(1, NUMBER_MAX + 1), key=lambda n: (values.get(n, 0), -n), reverse=True)


def frequency(draws: list[dict], field: str = "numbers") -> Counter:
    c = Counter()
    for draw in draws:
        if field == "numbers":
            c.update(draw["numbers"])
        else:
            c.update([draw[field]])
    return c


def omission(draws: list[dict], field: str = "numbers") -> dict[int, int]:
    last_seen = {n: None for n in range(1, NUMBER_MAX + 1)}
    for idx, draw in enumerate(draws):
        nums = draw["numbers"] if field == "numbers" else [draw[field]]
        for n in nums:
            last_seen[n] = idx
    end = len(draws) - 1
    return {n: len(draws) if idx is None else end - idx for n, idx in last_seen.items()}


def bayesian_frequency_scores(draws: list[dict], field: str = "numbers", alpha: float = 1.0, window: int | None = None) -> dict[int, float]:
    sample = draws[-window:] if window else draws
    counts = frequency(sample, field=field)
    observations = (len(sample) * MAIN_DRAW_SIZE) if field == "numbers" else len(sample)
    denominator = observations + alpha * NUMBER_MAX
    values = {n: (counts.get(n, 0) + alpha) / denominator for n in range(1, NUMBER_MAX + 1)}
    return normalize_map(values)


def ewma_frequency_scores(draws: list[dict], field: str = "numbers", half_life: float = 18.0) -> dict[int, float]:
    values = {n: 0.0 for n in range(1, NUMBER_MAX + 1)}
    if not draws:
        return values
    decay = 0.5 ** (1.0 / max(half_life, 1.0))
    weight = 1.0
    for draw in reversed(draws):
        nums = draw["numbers"] if field == "numbers" else [draw[field]]
        for n in nums:
            values[n] += weight
        weight *= decay
        if weight < 0.002:
            break
    return normalize_map(values)


def number_gap_history(draws: list[dict], field: str = "numbers") -> dict[int, list[int]]:
    seen_at = {n: None for n in range(1, NUMBER_MAX + 1)}
    gaps = {n: [] for n in range(1, NUMBER_MAX + 1)}
    for idx, draw in enumerate(draws):
        nums = draw["numbers"] if field == "numbers" else [draw[field]]
        for n in nums:
            if seen_at[n] is not None:
                gaps[n].append(idx - seen_at[n])
            seen_at[n] = idx
    return gaps


def gap_hazard_scores(draws: list[dict], field: str = "numbers") -> dict[int, float]:
    current = omission(draws, field=field)
    gaps = number_gap_history(draws, field=field)
    values = {}
    for n in range(1, NUMBER_MAX + 1):
        history = gaps.get(n) or []
        if len(history) < 3:
            expected_gap = NUMBER_MAX / (MAIN_DRAW_SIZE if field == "numbers" else 1)
        else:
            expected_gap = mean(history[-24:])
        ratio = current[n] / max(expected_gap, 1.0)
        values[n] = min(ratio, 2.5)
    return normalize_map(values)


def markov_transition_scores(draws: list[dict]) -> dict[int, float]:
    if len(draws) < 3:
        return {n: 0.0 for n in range(1, NUMBER_MAX + 1)}
    last_numbers = set(draws[-1]["numbers"])
    values = defaultdict(float)
    for idx in range(len(draws) - 1):
        current = set(draws[idx]["numbers"])
        nxt = set(draws[idx + 1]["numbers"])
        overlap = len(current & last_numbers)
        if overlap:
            for n in nxt:
                values[n] += overlap
    return normalize_map({n: values.get(n, 0.0) for n in range(1, NUMBER_MAX + 1)})


def chi_square_balance_scores(draws: list[dict], window: int = 60) -> dict[int, float]:
    sample = draws[-window:]
    expected_zone = len(sample) * MAIN_DRAW_SIZE / 5
    expected_tail = len(sample) * MAIN_DRAW_SIZE / 10
    zone_counts = Counter(zone_label(n) for draw in sample for n in draw["numbers"])
    tail_counts = Counter(n % 10 for draw in sample for n in draw["numbers"])
    values = {}
    for n in range(1, NUMBER_MAX + 1):
        zone_gap = max(expected_zone - zone_counts[zone_label(n)], 0) / max(expected_zone, 1)
        tail_gap = max(expected_tail - tail_counts[n % 10], 0) / max(expected_tail, 1)
        values[n] = 0.58 * zone_gap + 0.42 * tail_gap
    return normalize_map(values)


def mirror_scores(draws: list[dict]) -> dict[int, float]:
    recent = set(n for draw in draws[-6:] for n in draw["numbers"])
    values = {}
    for n in range(1, NUMBER_MAX + 1):
        mirrors = {50 - n}
        if n <= 25:
            mirrors.add(n + 24)
        else:
            mirrors.add(n - 24)
        values[n] = sum(1 for m in mirrors if 1 <= m <= NUMBER_MAX and m in recent)
    return normalize_map(values)


def zone_label(number: int) -> str:
    if number <= 10:
        return "01-10"
    if number <= 20:
        return "11-20"
    if number <= 30:
        return "21-30"
    if number <= 40:
        return "31-40"
    return "41-49"


def component_scores(draws: list[dict]) -> tuple[dict[str, dict[int, float]], dict[int, int], dict[int, list[str]]]:
    recent5 = draws[-5:]
    recent10 = draws[-10:]
    recent20 = draws[-20:]
    recent50 = draws[-50:]
    recent100 = draws[-100:]
    all_omission = omission(draws)
    reasons = defaultdict(list)
    components = {
        "heat_short": normalize_map({n: frequency(recent10).get(n, 0) for n in range(1, NUMBER_MAX + 1)}),
        "heat_mid": normalize_map({n: frequency(recent50).get(n, 0) for n in range(1, NUMBER_MAX + 1)}),
        "heat_long": normalize_map({n: frequency(draws[-720:]).get(n, 0) for n in range(1, NUMBER_MAX + 1)}),
        "bayes_global": bayesian_frequency_scores(draws, alpha=1.25, window=900),
        "ewma_fast": ewma_frequency_scores(draws, half_life=14),
        "ewma_slow": ewma_frequency_scores(draws, half_life=64),
        "omission": normalize_map(all_omission),
        "gap_hazard": gap_hazard_scores(draws),
    }

    last_numbers = set(draws[-1]["numbers"])
    pair_scores = defaultdict(float)
    for draw in draws[:-1]:
        nums = set(draw["numbers"])
        overlap = len(nums & last_numbers)
        if overlap:
            for n in nums - last_numbers:
                pair_scores[n] += overlap
    components["pair"] = normalize_map({n: pair_scores.get(n, 0) for n in range(1, NUMBER_MAX + 1)})
    components["markov_transition"] = markov_transition_scores(draws)

    recent_zone = Counter(zone_label(n) for draw in recent20 for n in draw["numbers"])
    recent_tail = Counter(n % 10 for draw in recent20 for n in draw["numbers"])
    zone_expected = len(recent20) * MAIN_DRAW_SIZE / 5
    tail_expected = len(recent20) * MAIN_DRAW_SIZE / 10
    tail_zone_scores = {}
    for n in range(1, NUMBER_MAX + 1):
        zone_deficit = max(zone_expected - recent_zone[zone_label(n)], 0) / max(zone_expected, 1)
        tail_deficit = max(tail_expected - recent_tail[n % 10], 0) / max(tail_expected, 1)
        tail_zone_scores[n] = 0.55 * zone_deficit + 0.45 * tail_deficit
    components["tail_zone"] = normalize_map(tail_zone_scores)
    components["chi_square_balance"] = chi_square_balance_scores(draws)

    repeat_neighbor = {}
    for n in range(1, NUMBER_MAX + 1):
        score = 0.0
        if n in last_numbers:
            score += 0.60
        if any(abs(n - last) == 1 for last in last_numbers):
            score += 0.35
        if n in set(draws[-2]["numbers"]) if len(draws) >= 2 else False:
            score += 0.15
        repeat_neighbor[n] = score
    components["repeat_neighbor"] = normalize_map(repeat_neighbor)
    components["mirror"] = mirror_scores(draws)

    special_recent = frequency(recent100, field="special")
    special_bridge = {n: special_recent.get(n, 0) for n in range(1, NUMBER_MAX + 1)}
    components["special_bridge"] = normalize_map(special_bridge)

    for n in range(1, NUMBER_MAX + 1):
        if components["heat_short"][n] >= 0.75:
            reasons[n].append("近十期熱度高")
        if components["heat_mid"][n] >= 0.75:
            reasons[n].append("中期趨勢強")
        if components["bayes_global"][n] >= 0.72:
            reasons[n].append("Bayesian平滑高分")
        if components["ewma_fast"][n] >= 0.72:
            reasons[n].append("EWMA短週期上升")
        if components["ewma_slow"][n] >= 0.72:
            reasons[n].append("EWMA長週期穩定")
        if components["omission"][n] >= 0.78:
            reasons[n].append("遺漏補償")
        if components["gap_hazard"][n] >= 0.72:
            reasons[n].append("間隔hazard偏高")
        if components["pair"][n] >= 0.72:
            reasons[n].append("上期關聯搭配")
        if components["markov_transition"][n] >= 0.72:
            reasons[n].append("Markov轉移關聯")
        if components["tail_zone"][n] >= 0.68:
            reasons[n].append("尾數區間補位")
        if components["chi_square_balance"][n] >= 0.70:
            reasons[n].append("區間卡方回補")
        if components["mirror"][n] >= 0.70:
            reasons[n].append("鏡像關聯")
        if components["special_bridge"][n] >= 0.72:
            reasons[n].append("特別號轉主號觀察")
    return components, all_omission, reasons


def special_component_scores(draws: list[dict]) -> tuple[dict[str, dict[int, float]], dict[int, int], dict[int, list[str]]]:
    recent10 = draws[-10:]
    recent50 = draws[-50:]
    all_omission = omission(draws, field="special")
    reasons = defaultdict(list)
    main_recent = frequency(draws[-80:])
    components = {
        "special_short": normalize_map({n: frequency(recent10, field="special").get(n, 0) for n in range(1, NUMBER_MAX + 1)}),
        "special_mid": normalize_map({n: frequency(recent50, field="special").get(n, 0) for n in range(1, NUMBER_MAX + 1)}),
        "special_long": normalize_map({n: frequency(draws[-720:], field="special").get(n, 0) for n in range(1, NUMBER_MAX + 1)}),
        "special_bayes": bayesian_frequency_scores(draws, field="special", alpha=1.15, window=900),
        "special_ewma": ewma_frequency_scores(draws, field="special", half_life=36),
        "special_omission": normalize_map(all_omission),
        "special_gap_hazard": gap_hazard_scores(draws, field="special"),
        "main_bridge": normalize_map({n: main_recent.get(n, 0) for n in range(1, NUMBER_MAX + 1)}),
    }
    for n in range(1, NUMBER_MAX + 1):
        if components["special_short"][n] >= 0.75:
            reasons[n].append("特別號短線熱")
        if components["special_bayes"][n] >= 0.72:
            reasons[n].append("特別號Bayesian平滑")
        if components["special_ewma"][n] >= 0.72:
            reasons[n].append("特別號EWMA上升")
        if components["special_omission"][n] >= 0.78:
            reasons[n].append("特別號遺漏補償")
        if components["special_gap_hazard"][n] >= 0.72:
            reasons[n].append("特別號間隔hazard")
        if components["main_bridge"][n] >= 0.76:
            reasons[n].append("主號熱度轉特別號")
    return components, all_omission, reasons


def latest_settled_prediction(conn: sqlite3.Connection) -> dict | None:
    row = conn.execute(
        """
        SELECT based_on_period, target_period, actual_period, actual_date,
               actual_numbers_json, actual_special, candidates_json, special_candidates_json,
               strong_pack_hits_json, top6_hits, top12_hits, top18_hits,
               special_top1_hit, special_top3_hit
        FROM predictions_lotto649
        WHERE status='settled'
        ORDER BY actual_period DESC, id DESC
        LIMIT 1
        """
    ).fetchone()
    if not row:
        return None
    return {
        "based_on_period": row[0],
        "target_period": row[1],
        "actual_period": row[2],
        "actual_date": row[3],
        "actual_numbers": json.loads(row[4] or "[]"),
        "actual_special": row[5],
        "candidate_numbers": [item["number"] for item in json.loads(row[6] or "[]")],
        "special_candidates": [item["number"] for item in json.loads(row[7] or "[]")],
        "strong_pack_hits": json.loads(row[8] or "{}"),
        "top6_hits": row[9],
        "top12_hits": row[10],
        "top18_hits": row[11],
        "special_top1_hit": row[12],
        "special_top3_hit": row[13],
    }


def failure_review(conn: sqlite3.Connection) -> dict:
    settled = latest_settled_prediction(conn)
    if not settled:
        return {"has_review": False, "severity": "none", "actions": []}
    severity = "normal"
    actions = []
    top6_hits = int(settled["top6_hits"] or 0)
    top12_hits = int(settled["top12_hits"] or 0)
    special_top3_hit = int(settled["special_top3_hit"] or 0)
    if top6_hits == 0 or (top12_hits <= 1 and not special_top3_hit):
        severity = "critical"
        actions = [
            "強制降低短線熱號、上期關聯、長期鈍化模型",
            "提高中期趨勢、gap hazard、EWMA慢週期、遺漏補償",
            "主號與特別號都套用上一期失手號碼懲罰",
        ]
    elif top6_hits <= 1 or top12_hits <= 2 or not special_top3_hit:
        severity = "warning"
        actions = [
            "降低上一期 Top6/Top12 未命中號碼的延續權重",
            "提高中期趨勢、EWMA慢週期、gap hazard 與遺漏補償",
            "特別號 Top3 失手時同步懲罰前次特別號候選",
        ]
    return {
        "has_review": True,
        "severity": severity,
        "actions": actions,
        "last_settled": settled,
    }


def apply_failure_adjustment(weights: dict[str, float], review: dict) -> dict[str, float]:
    adjusted = dict(weights)
    if review.get("severity") == "critical":
        multipliers = {
            "heat_short": 0.60,
            "heat_long": 0.62,
            "bayes_global": 0.70,
            "pair": 0.62,
            "markov_transition": 0.66,
            "repeat_neighbor": 0.72,
            "heat_mid": 1.20,
            "ewma_slow": 1.16,
            "gap_hazard": 1.18,
            "omission": 1.16,
            "mirror": 1.08,
            "special_bridge": 0.82,
        }
    elif review.get("severity") == "warning":
        multipliers = {
            "heat_short": 0.76,
            "heat_long": 0.78,
            "pair": 0.80,
            "markov_transition": 0.84,
            "heat_mid": 1.12,
            "ewma_slow": 1.10,
            "gap_hazard": 1.10,
            "omission": 1.08,
            "mirror": 1.04,
        }
    else:
        multipliers = {}
    for name, multiplier in multipliers.items():
        adjusted[name] = adjusted.get(name, 0) * multiplier
    total = sum(adjusted.values()) or 1
    return {name: round(value / total, 4) for name, value in adjusted.items()}


def score_numbers(draws: list[dict], model_weights: dict[str, float], review: dict | None = None) -> list[dict]:
    components, all_omission, reasons = component_scores(draws)
    total_weight = sum(model_weights.values()) or 1
    score = defaultdict(float)
    for name, values in components.items():
        weight = model_weights.get(name, 0) / total_weight
        for n in range(1, NUMBER_MAX + 1):
            score[n] += weight * values.get(n, 0)
    consensus = defaultdict(float)
    for name, values in components.items():
        weight = model_weights.get(name, 0) / total_weight
        for rank, n in enumerate(rank_values(values), start=1):
            consensus[n] += weight * ((NUMBER_MAX + 1 - rank) / NUMBER_MAX)
    consensus = normalize_map(dict(consensus))
    overdue = normalize_map(all_omission)
    for n in range(1, NUMBER_MAX + 1):
        score[n] = score[n] * 0.86 + consensus[n] * 0.10 + overdue[n] * 0.04
    if review and review.get("severity") in {"critical", "warning"}:
        settled = review.get("last_settled", {})
        actual_numbers = set(settled.get("actual_numbers") or [])
        previous = settled.get("candidate_numbers") or []
        failed_top6 = set(previous[:6]) - actual_numbers
        failed_top12 = set(previous[:12]) - actual_numbers
        hard_penalty = 0.48 if review.get("severity") == "critical" else 0.62
        soft_penalty = 0.68 if review.get("severity") == "critical" else 0.82
        for n in failed_top12:
            score[n] *= hard_penalty if n in failed_top6 else soft_penalty
            reasons[n].append("上期失手降權")

    ranked = rank_values(score)
    max_score = max(score.values())
    min_score = min(score.values())
    candidates = []
    for n in ranked:
        confidence = 50 if max_score == min_score else 50 + (score[n] - min_score) / (max_score - min_score) * 49
        candidates.append(
            {
                "number": n,
                "score": round(score[n], 5),
                "confidence_index": round(confidence, 1),
                "omission": all_omission[n],
                "zone": zone_label(n),
                "tail": n % 10,
                "reasons": reasons[n][:5],
            }
        )
    return candidates


def score_special_numbers(draws: list[dict], review: dict | None = None) -> list[dict]:
    components, all_omission, reasons = special_component_scores(draws)
    total_weight = sum(SPECIAL_MODEL_WEIGHTS.values()) or 1
    score = defaultdict(float)
    for name, values in components.items():
        weight = SPECIAL_MODEL_WEIGHTS.get(name, 0) / total_weight
        for n in range(1, NUMBER_MAX + 1):
            score[n] += weight * values.get(n, 0)
    if review and review.get("severity") in {"critical", "warning"}:
        settled = review.get("last_settled", {})
        actual_special = settled.get("actual_special")
        previous_specials = settled.get("special_candidates") or []
        if actual_special not in previous_specials[:3]:
            penalty = 0.50 if review.get("severity") == "critical" else 0.66
            for n in previous_specials[:3]:
                score[n] *= penalty
                reasons[n].append("上期特別號失手降權")
    ranked = rank_values(score)
    max_score = max(score.values())
    min_score = min(score.values())
    candidates = []
    for n in ranked:
        confidence = 50 if max_score == min_score else 50 + (score[n] - min_score) / (max_score - min_score) * 49
        candidates.append(
            {
                "number": n,
                "score": round(score[n], 5),
                "confidence_index": round(confidence, 1),
                "omission": all_omission[n],
                "zone": zone_label(n),
                "tail": n % 10,
                "reasons": reasons[n][:5],
            }
        )
    return candidates


def strategy_rankings(draws: list[dict], model_weights: dict[str, float] | None = None) -> dict[str, list[int]]:
    weights = model_weights or DEFAULT_MODEL_WEIGHTS
    components, _, _ = component_scores(draws)
    ensemble = {n: 0.0 for n in range(1, NUMBER_MAX + 1)}
    total_weight = sum(weights.values()) or 1
    for name, values in components.items():
        for n in range(1, NUMBER_MAX + 1):
            ensemble[n] += (weights.get(name, 0) / total_weight) * values.get(n, 0)
    rankings = {name: rank_values(values) for name, values in components.items()}
    rankings["ensemble"] = rank_values(ensemble)
    return rankings


def backtest(draws: list[dict], rounds: int = 520, top_sizes: tuple[int, ...] = (6, 12, 18)) -> dict:
    if len(draws) < 150:
        return {"rounds": 0, "strategies": {}, "note": "資料不足，略過回測。"}
    start = max(120, len(draws) - rounds - 1)
    stats = defaultdict(lambda: {f"top{size}_hits": 0 for size in top_sizes} | {"rounds": 0})
    for idx in range(start, len(draws) - 1):
        train = draws[: idx + 1]
        actual = set(draws[idx + 1]["numbers"])
        rankings = strategy_rankings(train)
        for strategy, ranking in rankings.items():
            stats[strategy]["rounds"] += 1
            for size in top_sizes:
                stats[strategy][f"top{size}_hits"] += len(set(ranking[:size]) & actual)
    random_expectation = {size: round(MAIN_DRAW_SIZE * size / NUMBER_MAX, 3) for size in top_sizes}
    result = {}
    for strategy, strategy_stats in stats.items():
        strategy_rounds = strategy_stats["rounds"]
        result[strategy] = {"rounds": strategy_rounds}
        for size in top_sizes:
            avg = strategy_stats[f"top{size}_hits"] / strategy_rounds
            result[strategy][f"top{size}_avg_hits"] = round(avg, 3)
            result[strategy][f"top{size}_edge_vs_random"] = round(avg - random_expectation[size], 3)
    return {
        "rounds": next(iter(stats.values()))["rounds"],
        "random_expectation": random_expectation,
        "strategies": result,
    }


def weighted_ensemble_backtest(draws: list[dict], model_weights: dict[str, float], rounds: int = 520, top_sizes: tuple[int, ...] = (6, 12, 18)) -> dict:
    if len(draws) < 150:
        return {"rounds": 0, "note": "資料不足，略過最終權重回測。"}
    start = max(120, len(draws) - rounds - 1)
    stats = {f"top{size}_hits": 0 for size in top_sizes} | {"rounds": 0}
    for idx in range(start, len(draws) - 1):
        train = draws[: idx + 1]
        actual = set(draws[idx + 1]["numbers"])
        ranking = strategy_rankings(train, model_weights=model_weights)["ensemble"]
        stats["rounds"] += 1
        for size in top_sizes:
            stats[f"top{size}_hits"] += len(set(ranking[:size]) & actual)
    random_expectation = {size: round(MAIN_DRAW_SIZE * size / NUMBER_MAX, 3) for size in top_sizes}
    result = {"rounds": stats["rounds"], "random_expectation": random_expectation}
    for size in top_sizes:
        avg = stats[f"top{size}_hits"] / max(stats["rounds"], 1)
        result[f"top{size}_avg_hits"] = round(avg, 3)
        result[f"top{size}_edge_vs_random"] = round(avg - random_expectation[size], 3)
    return result


def edge_metric(backtest_result: dict, model_name: str, size: int) -> float:
    strategies = backtest_result.get("strategies", {})
    return float(strategies.get(model_name, {}).get(f"top{size}_edge_vs_random", 0) or 0)


def calibration_diagnostics(full_backtest: dict, recent_backtest: dict | None = None) -> dict:
    recent_backtest = recent_backtest or full_backtest
    models = {}
    for name, base_weight in DEFAULT_MODEL_WEIGHTS.items():
        full6 = edge_metric(full_backtest, name, 6)
        full12 = edge_metric(full_backtest, name, 12)
        full18 = edge_metric(full_backtest, name, 18)
        recent6 = edge_metric(recent_backtest, name, 6)
        recent12 = edge_metric(recent_backtest, name, 12)
        recent18 = edge_metric(recent_backtest, name, 18)
        edge_score = (
            0.10 * full6
            + 0.18 * full12
            + 0.14 * full18
            + 0.18 * recent6
            + 0.26 * recent12
            + 0.14 * recent18
        )
        votes = sum(edge > 0 for edge in [full6, full12, full18, recent6, recent12, recent18])
        if edge_score >= 0.050:
            multiplier = 1.65
            tier = "promote"
        elif edge_score >= 0.020:
            multiplier = 1.28
            tier = "support"
        elif edge_score >= 0.000:
            multiplier = 1.00
            tier = "neutral"
        elif edge_score >= -0.035:
            multiplier = 0.55
            tier = "shrink"
        else:
            multiplier = 0.24
            tier = "quarantine"
        if votes <= 1:
            multiplier *= 0.55
            tier = "quarantine"
        elif votes == 2:
            multiplier *= 0.78
        models[name] = {
            "base_weight": base_weight,
            "edge_score": round(edge_score, 5),
            "positive_votes": votes,
            "tier": tier,
            "multiplier": round(multiplier, 4),
            "full_edges": {"top6": full6, "top12": full12, "top18": full18},
            "recent_edges": {"top6": recent6, "top12": recent12, "top18": recent18},
        }
    return {
        "method": "dual_window_edge_shrink_v3",
        "full_rounds": full_backtest.get("rounds", 0),
        "recent_rounds": recent_backtest.get("rounds", 0),
        "models": models,
    }


def calibrated_weights(full_backtest: dict, recent_backtest: dict | None = None) -> dict[str, float]:
    diagnostics = calibration_diagnostics(full_backtest, recent_backtest)
    raw_weights = {}
    for name, info in diagnostics["models"].items():
        raw_weights[name] = DEFAULT_MODEL_WEIGHTS[name] * info["multiplier"]
    total_raw = sum(raw_weights.values()) or 1.0
    normalized = {name: value / total_raw for name, value in raw_weights.items()}
    capped = {}
    for name, value in normalized.items():
        tier = diagnostics["models"][name]["tier"]
        cap = 0.150 if tier in {"promote", "support"} else 0.095
        floor = 0.010 if tier == "quarantine" else 0.018
        capped[name] = min(max(value, floor), cap)
    total = sum(capped.values()) or 1.0
    return {name: round(value / total, 4) for name, value in capped.items()}


def diversity_penalty(selected: list[int], candidate: int) -> float:
    if not selected:
        return 0.0
    penalty = 0.0
    if any(n % 10 == candidate % 10 for n in selected):
        penalty += 0.030
    if any(abs(n - candidate) == 1 for n in selected):
        penalty += 0.020
    if sum(1 for n in selected if zone_label(n) == zone_label(candidate)) >= 2:
        penalty += 0.035
    return penalty


def historical_combo_profile(draws: list[dict], window: int = 720) -> dict:
    sample = draws[-window:] if len(draws) > window else draws
    sums = [sum(draw["numbers"]) for draw in sample]
    odd_counts = [sum(1 for n in draw["numbers"] if n % 2) for draw in sample]
    zone_modes = Counter(tuple(sorted(Counter(zone_label(n) for n in draw["numbers"]).items())) for draw in sample)
    return {
        "sum_mean": mean(sums),
        "sum_std": max((sum((value - mean(sums)) ** 2 for value in sums) / len(sums)) ** 0.5, 1.0),
        "odd_common": {count for count, _ in Counter(odd_counts).most_common(3)},
        "zone_common": {item for item, _ in zone_modes.most_common(10)},
    }


def combo_penalty(numbers: list[int], profile: dict) -> float:
    nums = sorted(numbers)
    total = sum(nums)
    z = abs(total - profile["sum_mean"]) / profile["sum_std"]
    odd_count = sum(1 for n in nums if n % 2)
    zones = Counter(zone_label(n) for n in nums)
    tails = Counter(n % 10 for n in nums)
    consecutive_pairs = sum(1 for a, b in zip(nums, nums[1:]) if b - a == 1)
    penalty = 0.0
    if z > 1.15:
        penalty += (z - 1.15) * 0.22
    if odd_count not in profile["odd_common"]:
        penalty += 0.16
    if max(zones.values()) >= 4:
        penalty += 0.20
    if max(tails.values()) >= 3:
        penalty += 0.16
    if consecutive_pairs > 2:
        penalty += 0.15 * (consecutive_pairs - 2)
    zone_key = tuple(sorted(zones.items()))
    if zone_key not in profile["zone_common"]:
        penalty += 0.04
    return penalty


def optimized_group(candidates: list[dict], size: int) -> list[int]:
    score_by_number = {item["number"]: item["score"] for item in candidates}
    selected = []
    pool = [item["number"] for item in candidates[:28]]
    while len(selected) < size and pool:
        best = max(pool, key=lambda n: score_by_number[n] - diversity_penalty(selected, n))
        selected.append(best)
        pool.remove(best)
    return sorted(selected)


def probability_at_least(pool_size: int, winners: int, pick_size: int, hit_goal: int) -> float:
    total = math.comb(pool_size, pick_size)
    miss = 0
    for hits in range(0, hit_goal):
        if hits <= winners and pick_size - hits <= pool_size - winners:
            miss += math.comb(winners, hits) * math.comb(pool_size - winners, pick_size - hits)
    return 1 - miss / total


def build_strong_prediction_packs(candidates: list[dict], special_candidates: list[dict]) -> dict:
    score_by_number = {item["number"]: item["score"] for item in candidates}

    def pack(name: str, hit_goal: int, numbers: list[int]) -> dict:
        prob = probability_at_least(NUMBER_MAX, MAIN_DRAW_SIZE, len(numbers), hit_goal)
        return {
            "name": name,
            "hit_goal": hit_goal,
            "numbers": numbers,
            "score_sum": round(sum(score_by_number[n] for n in numbers), 5),
            "avg_score": round(sum(score_by_number[n] for n in numbers) / len(numbers), 5),
            "theoretical_probability": round(prob, 6),
            "zones": dict(Counter(zone_label(n) for n in numbers)),
            "tails": dict(Counter(n % 10 for n in numbers)),
        }

    return {
        "strong_single": pack("最強單支", 1, [candidates[0]["number"]]),
        "two_hit_one": pack("最強2中1", 1, optimized_group(candidates, 2)),
        "three_hit_one": pack("最強3中1", 1, optimized_group(candidates, 3)),
        "five_hit_two": pack("最強5中2", 2, optimized_group(candidates, 5)),
        "nine_hit_three": pack("最強9中3", 3, optimized_group(candidates, 9)),
        "special_single": {
            "name": "特別號最強單支",
            "hit_goal": 1,
            "numbers": [special_candidates[0]["number"]],
            "theoretical_probability": round(1 / NUMBER_MAX, 6),
            "avg_score": special_candidates[0]["score"],
        },
        "special_three_watch": {
            "name": "特別號3碼觀察",
            "hit_goal": 1,
            "numbers": [item["number"] for item in special_candidates[:3]],
            "theoretical_probability": round(3 / NUMBER_MAX, 6),
            "avg_score": round(mean(item["score"] for item in special_candidates[:3]), 5),
        },
    }


def build_sets(candidates: list[dict], special_candidates: list[dict], draws: list[dict]) -> list[dict]:
    score_by_number = {item["number"]: item["score"] for item in candidates}
    rank_bonus = {item["number"]: max(0, 28 - idx) / 28 for idx, item in enumerate(candidates[:28])}
    profile = historical_combo_profile(draws)
    pool = [item["number"] for item in candidates[:22]]
    overdue_pool = [item["number"] for item in sorted(candidates, key=lambda x: (x["omission"], x["score"]), reverse=True)[:8]]
    pool = sorted(set(pool + overdue_pool))
    scored = []
    for nums in combinations(pool, 6):
        nums = sorted(nums)
        raw = sum(score_by_number[n] for n in nums) + 0.035 * sum(rank_bonus.get(n, 0) for n in nums)
        quality = raw - combo_penalty(nums, profile)
        scored.append((quality, nums))
    scored.sort(reverse=True, key=lambda item: item[0])
    sets = []
    seen = set()
    for _, numbers in scored:
        key = tuple(numbers)
        if key in seen:
            continue
        if any(len(set(numbers) & set(item["numbers"])) >= 5 for item in sets):
            continue
        seen.add(key)
        special = next(item["number"] for item in special_candidates if item["number"] not in numbers)
        sets.append({"name": f"建議組合{len(sets) + 1}", "numbers": numbers, "special": special})
        if len(sets) >= 8:
            break
    return sets


def analyze(conn: sqlite3.Connection) -> dict:
    draws = load_draws(conn)
    if len(draws) < 150:
        raise RuntimeError("大樂透歷史資料不足，禁止產生正式預測")
    latest = draws[-1]
    review = failure_review(conn)
    initial_backtest = backtest(draws)
    recent_backtest = backtest(draws, rounds=180)
    calibration = calibration_diagnostics(initial_backtest, recent_backtest)
    weights = apply_failure_adjustment(calibrated_weights(initial_backtest, recent_backtest), review)
    adaptive_backtest = weighted_ensemble_backtest(draws, weights)
    candidates = score_numbers(draws, weights, review)
    special_candidates = score_special_numbers(draws, review)
    strong_packs = build_strong_prediction_packs(candidates, special_candidates)
    suggested_sets = build_sets(candidates, special_candidates, draws)
    health = history_health(conn)
    target_date = next_draw_date(latest["draw_date"])
    return {
        "system": "台灣大樂透鐵律預測系統",
        "version": "lotto649_ironlaw_cloud_v7_20260706_strict_desktop_mobile_sync",
        "generated_at": taipei_now().isoformat(timespec="seconds"),
        "history_info": health,
        "latest_draw": latest,
        "target_period": latest["period"] + 1,
        "target_date": target_date,
        "data_freshness": health.get("freshness", {}),
        "failure_review": review,
        "backtest": initial_backtest,
        "recent_backtest": recent_backtest,
        "adaptive_backtest": adaptive_backtest,
        "calibration": calibration,
        "model_weights": weights,
        "candidates": candidates,
        "special_candidates": special_candidates,
        "suggested_sets": suggested_sets,
        "strong_prediction_packs": strong_packs,
        "model_upgrade_notes": [
            "v3改為雙回測校準：長期520期與近期180期同步評估",
            "新增最終權重520期回測，最終綜合模型沒通過就不准輸出",
            "v4新增失手回饋：Top6低命中或特別號失手會立刻觸發降權，不等爆掉才調整",
            "v4新增特別號失手懲罰，前次特別號Top3沒中會同步降權",
            "v5新增每日雲端全系統掃描：每天自動更新、檢測、失敗改跑全量修復並同步手機版",
            "v6戰報規格對齊539：核心決策、逐號驗算、短包強牌、低機率避險、每日更新鐵律、模型滾動調整完整輸出",
            "v7新增電腦版、手機版、GitHub Pages逐檔同步守門：不同步或未更新一律判定失敗",
            "負邊際模型進入shrink/quarantine，不再平均分配權重拖累排序",
            "高正邊際模型才進入support/promote，並設定權重上限避免單一模型過擬合",
            "主號排序加入高權重模型共識分數與少量遺漏補償，降低單點噪音",
            "保留Dirichlet/Bayesian、EWMA、Markov、gap hazard、卡方區間/尾數與組合搜尋",
            "建議組合繼續使用候選池搜尋，加入總和、奇偶、區間、尾數、連號約束",
        ],
        "iron_laws": [
            "資料先行：先建立 SQLite 與 CSV 全歷史資料庫，再產生候選號碼",
            "多窗口分析：近5、10、20、50、100期與長期資料同步評分",
            "強牌分層：單支、2中1、3中1、5中2、9中3，另列特別號層",
            "上一期必結算：保留 Top6、Top12、Top18、強牌組與特別號命中，低命中會觸發下一期回饋降權",
            "交叉驗證：年度官方檔與月查詢 API 互補",
            "戰報透明：輸出 latest_analysis.json、HTML、Markdown 與手機雲端版",
            "不迷信單一模型：熱度、Bayesian、EWMA、Markov、gap hazard、遺漏、關聯、尾數區間與雙回測校準共同決策",
            "資料新鮮度：標示最新日期、理論應更新日期與落後天數",
        ],
        "disclaimer": "本系統為歷史統計與回測研究，不保證開獎命中或獲利，請量力而為。",
    }


def settle_pending_predictions(conn: sqlite3.Connection) -> int:
    draws = {draw["period"]: draw for draw in load_draws(conn)}
    rows = conn.execute(
        """
        SELECT id, based_on_period, target_period, candidates_json, special_candidates_json,
               suggested_sets_json, strong_packs_json
        FROM predictions_lotto649
        WHERE status='pending'
        ORDER BY based_on_period
        """
    ).fetchall()
    settled_count = 0
    for row in rows:
        prediction_id, based_on_period, target_period = row[0], row[1], row[2]
        actual = draws.get(target_period) or next((d for d in draws.values() if d["period"] > based_on_period), None)
        if not actual:
            continue
        candidates = json.loads(row[3] or "[]")
        special_candidates = json.loads(row[4] or "[]")
        suggested_sets = json.loads(row[5] or "[]")
        strong_packs = json.loads(row[6] or "{}")
        actual_set = set(actual["numbers"])
        top6 = [item["number"] for item in candidates[:6]]
        top12 = [item["number"] for item in candidates[:12]]
        top18 = [item["number"] for item in candidates[:18]]
        set_hits = []
        for item in suggested_sets:
            nums = item.get("numbers") or []
            set_hits.append(
                {
                    "name": item.get("name"),
                    "numbers": nums,
                    "special": item.get("special"),
                    "main_hits": len(set(nums) & actual_set),
                    "special_hit": int(item.get("special") == actual["special"]),
                    "hit_numbers": sorted(set(nums) & actual_set),
                }
            )
        strong_hits = {}
        for key, pack in strong_packs.items():
            nums = pack.get("numbers") or []
            if key.startswith("special"):
                hits = int(actual["special"] in nums)
            else:
                hits = len(set(nums) & actual_set)
            strong_hits[key] = {
                "name": pack.get("name"),
                "numbers": nums,
                "hit_goal": pack.get("hit_goal"),
                "hits": hits,
                "passed": hits >= int(pack.get("hit_goal") or 1),
            }
        conn.execute(
            """
            UPDATE predictions_lotto649
            SET settled_at=?, actual_period=?, actual_date=?, actual_numbers_json=?, actual_special=?,
                top6_hits=?, top12_hits=?, top18_hits=?,
                special_top1_hit=?, special_top3_hit=?,
                set_hits_json=?, strong_pack_hits_json=?, status='settled'
            WHERE id=?
            """,
            (
                taipei_now().isoformat(timespec="seconds"),
                actual["period"],
                actual["draw_date"],
                json.dumps(actual["numbers"], ensure_ascii=False),
                actual["special"],
                len(set(top6) & actual_set),
                len(set(top12) & actual_set),
                len(set(top18) & actual_set),
                int(special_candidates and special_candidates[0]["number"] == actual["special"]),
                int(actual["special"] in [item["number"] for item in special_candidates[:3]]),
                json.dumps(set_hits, ensure_ascii=False),
                json.dumps(strong_hits, ensure_ascii=False),
                prediction_id,
            ),
        )
        settled_count += 1
    conn.commit()
    return settled_count


def save_prediction(conn: sqlite3.Connection, analysis: dict) -> str:
    latest = analysis["latest_draw"]
    existing = conn.execute(
        "SELECT id, status FROM predictions_lotto649 WHERE based_on_period=?",
        (latest["period"],),
    ).fetchone()
    payload = (
        latest["period"],
        latest["draw_date"],
        analysis["target_period"],
        analysis["target_date"],
        json.dumps(analysis["candidates"], ensure_ascii=False),
        json.dumps(analysis["special_candidates"], ensure_ascii=False),
        json.dumps(analysis["suggested_sets"], ensure_ascii=False),
        json.dumps(analysis["strong_prediction_packs"], ensure_ascii=False),
        json.dumps(analysis["model_weights"], ensure_ascii=False),
        json.dumps(analysis["backtest"], ensure_ascii=False),
        analysis["generated_at"],
    )
    if existing and existing[1] == "settled":
        conn.execute(
            """
            INSERT INTO prediction_snapshots_lotto649 (
                based_on_period, based_on_date, target_period, target_date,
                candidates_json, special_candidates_json, suggested_sets_json,
                strong_packs_json, model_weights_json, backtest_json, created_at, snapshot_reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            payload + ("preserved_settled_prediction",),
        )
        conn.commit()
        return "preserved_settled"
    if existing:
        conn.execute(
            """
            UPDATE predictions_lotto649
            SET based_on_date=?, target_period=?, target_date=?,
                candidates_json=?, special_candidates_json=?, suggested_sets_json=?,
                strong_packs_json=?, model_weights_json=?, backtest_json=?, created_at=?,
                status='pending'
            WHERE based_on_period=?
            """,
            (
                latest["draw_date"],
                analysis["target_period"],
                analysis["target_date"],
                json.dumps(analysis["candidates"], ensure_ascii=False),
                json.dumps(analysis["special_candidates"], ensure_ascii=False),
                json.dumps(analysis["suggested_sets"], ensure_ascii=False),
                json.dumps(analysis["strong_prediction_packs"], ensure_ascii=False),
                json.dumps(analysis["model_weights"], ensure_ascii=False),
                json.dumps(analysis["backtest"], ensure_ascii=False),
                analysis["generated_at"],
                latest["period"],
            ),
        )
        status = "updated_pending"
    else:
        conn.execute(
            """
            INSERT INTO predictions_lotto649 (
                based_on_period, based_on_date, target_period, target_date,
                candidates_json, special_candidates_json, suggested_sets_json,
                strong_packs_json, model_weights_json, backtest_json, created_at, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
            """,
            payload,
        )
        status = "inserted"
    conn.commit()
    return status


def prediction_history(conn: sqlite3.Connection) -> dict:
    rows = conn.execute(
        """
        SELECT based_on_period, based_on_date, target_period, target_date,
               candidates_json, special_candidates_json, suggested_sets_json,
               strong_packs_json, actual_period, actual_date, actual_numbers_json, actual_special,
               top6_hits, top12_hits, top18_hits, special_top1_hit, special_top3_hit,
               set_hits_json, strong_pack_hits_json, status, created_at, settled_at
        FROM predictions_lotto649
        ORDER BY target_period DESC, id DESC
        """
    ).fetchall()
    periods = []
    for row in rows:
        candidates = json.loads(row[4] or "[]")
        special_candidates = json.loads(row[5] or "[]")
        actual_numbers = json.loads(row[10] or "[]")
        actual_set = set(actual_numbers)
        periods.append(
            {
                "based_on_period": row[0],
                "based_on_date": row[1],
                "target_period": row[2],
                "target_date": row[3],
                "top6": [item["number"] for item in candidates[:6]],
                "top12": [item["number"] for item in candidates[:12]],
                "top18": [item["number"] for item in candidates[:18]],
                "special_top3": [item["number"] for item in special_candidates[:3]],
                "suggested_sets": json.loads(row[6] or "[]"),
                "strong_packs": json.loads(row[7] or "{}"),
                "actual_period": row[8],
                "actual_date": row[9],
                "actual_numbers": actual_numbers,
                "actual_special": row[11],
                "top6_hits": row[12],
                "top12_hits": row[13],
                "top18_hits": row[14],
                "top12_hit_numbers": sorted(actual_set & set(item["number"] for item in candidates[:12])),
                "special_top1_hit": row[15],
                "special_top3_hit": row[16],
                "set_hits": json.loads(row[17] or "[]"),
                "strong_pack_hits": json.loads(row[18] or "{}"),
                "status": row[19],
                "created_at": row[20],
                "settled_at": row[21],
            }
        )
    return {
        "generated_at": taipei_now().isoformat(timespec="seconds"),
        "total_periods": len(periods),
        "settled_periods": sum(1 for item in periods if item["status"] == "settled"),
        "pending_periods": sum(1 for item in periods if item["status"] == "pending"),
        "periods": periods,
    }


def esc(value) -> str:
    return html.escape("" if value is None else str(value))


def render_markdown(analysis: dict, history: dict) -> str:
    latest = analysis["latest_draw"]
    freshness = analysis["data_freshness"]
    candidates = analysis["candidates"]
    special_candidates = analysis["special_candidates"]
    packs = analysis["strong_prediction_packs"]
    review = analysis.get("failure_review") or {}
    bt = analysis["backtest"]
    recent_bt = analysis.get("recent_backtest") or {}
    adaptive_bt = analysis.get("adaptive_backtest") or {}
    ensemble = bt.get("strategies", {}).get("ensemble", {})
    calibration = (analysis.get("calibration") or {}).get("models") or {}
    high_confidence = candidates[:6]
    avoid5 = [item["number"] for item in candidates[-5:]]
    avoid10 = [item["number"] for item in candidates[-10:]]
    avoid15 = [item["number"] for item in candidates[-15:]]

    def pack_numbers(key: str) -> list[int]:
        return packs.get(key, {}).get("numbers") or []

    def candidate_detail(item: dict) -> str:
        reasons = "、".join(item.get("reasons") or ["綜合排序"])
        return (
            f"排名 {candidates.index(item) + 1} / 信心 {item.get('confidence_index')} / "
            f"分數 {item.get('score')} / 遺漏 {item.get('omission')} / 區間 {item.get('zone')} / 尾 {item.get('tail')} / {reasons}"
        )

    lines = [
        "# 台灣大樂透鐵律戰報",
        "",
        f"- 產生時間：{analysis['generated_at']}",
        f"- 全歷史資料庫：{analysis['history_info']['first_date']} 至 {analysis['history_info']['latest_date']}，共 {analysis['history_info']['draw_count']} 期",
        f"- 最新期別：{latest['period']} ({latest['draw_date']})",
        f"- 最新本號：{fmt_numbers(latest['numbers'])} / 特別號 {latest['special']:02d}",
        f"- 目標期別：{analysis['target_period']}，預估開獎日：{analysis['target_date']}",
        f"- 資料新鮮度：{freshness.get('status')}，應有最新日 {freshness.get('expected_latest_date')}，落後 {freshness.get('lag_days')} 天",
        f"- 發布等級：{review.get('severity', 'normal')} / v7 539戰報規格 + 每日同步守門",
        f"- 風險等級：{'高' if review.get('severity') in {'critical', 'warning'} else '中'}",
        "- 提醒：本戰報是歷史統計與回測研究，不保證開出或獲利。",
        "",
        "## 核心決策",
        f"- 作戰結論：{'失手回饋強化' if review.get('severity') in {'critical', 'warning'} else '穩定觀察'} / 等級 {'乙級' if review.get('severity') == 'warning' else '觀察'}",
        f"- 明確獨支：{fmt_numbers(pack_numbers('strong_single'))}",
        f"- 明確2中1：{fmt_numbers(pack_numbers('two_hit_one'))}",
        f"- 明確3中1：{fmt_numbers(pack_numbers('three_hit_one'))}",
        f"- 明確5中2：{fmt_numbers(pack_numbers('five_hit_two'))}",
        f"- 明確9中3：{fmt_numbers(pack_numbers('nine_hit_three'))}",
        f"- 高機率信心牌：{fmt_numbers([item['number'] for item in high_confidence])}",
        f"- 防守避開：{fmt_numbers(avoid10)}",
        f"- 特別號核心：{fmt_numbers([item['number'] for item in special_candidates[:3]])}",
        "",
        "## 最強獨隻1中1",
        f"- 獨隻號碼：{fmt_numbers(pack_numbers('strong_single'))}",
        f"- 高信心加註：{fmt_numbers([item['number'] for item in candidates[:3]])}",
        "",
        "## 高機率信心牌特別強調",
    ]
    for item in high_confidence:
        lines.append(f"- {item['number']:02d}：{candidate_detail(item)} / 本期攻擊核心優先關注，仍需依風控分批使用。")
    lines.extend(["", "## 逐號多重驗算明細"])
    for item in candidates[:9]:
        lines.append(f"- {item['number']:02d}：版路 {item.get('zone')}區、尾{item.get('tail')}；來源 {'、'.join(item.get('reasons') or ['綜合排序'])}；{candidate_detail(item)}；守門通過")
    lines.extend(["", "## 獨支 / 2中1 / 3中1 短包超強信心精算"])
    for key, label in [("strong_single", "獨支1中1"), ("two_hit_one", "2中1"), ("three_hit_one", "3中1")]:
        pack = packs.get(key) or {}
        lines.append(f"- {label}：{fmt_numbers(pack.get('numbers', [])) or '-'} / 狀態 研究預測 / 理論機率 {float(pack.get('theoretical_probability', 0))*100:.2f}% / 平均分 {pack.get('avg_score', '-')}")
    lines.extend(["", "## 低機率避險包"])
    lines.append(f"- 5不中：{fmt_numbers(avoid5)} / 防守觀察 / 依目前模型低分排序")
    lines.append(f"- 10不中：{fmt_numbers(avoid10)} / 防守觀察 / 依目前模型低分排序")
    lines.append(f"- 15不中：{fmt_numbers(avoid15)} / 防守觀察 / 依目前模型低分排序")
    lines.extend(["", "## 低機率精準暫避", "- 已併入本戰報低機率避險包；低機率不等於絕對不開。"])
    lines.extend(
        [
            "",
            "## 每日更新鐵律時間表",
            "- 每日雲端掃描：台灣時間08:30自動更新資料、重算、檢測、同步手機版。",
            "- 開獎後追加：週二、週五台灣時間22:20與23:10追官方最新資料。",
            "- 更新失敗修復：latest失敗或自我檢測失敗，改跑全歷史全量重建。",
            "- 禁止事項：禁止未重新運算就沿用前一期預測；手機版必須與docs同步。",
            "",
            "## 下期精算前9名",
        ]
    )
    for idx, item in enumerate(candidates[:9], 1):
        lines.append(f"{idx}. {item['number']:02d} / 信心 {item['confidence_index']} / 成熟度 研究觀察 / 遺漏 {item['omission']}")
    lines.extend(["", "## 強牌組精算"])
    for key, pack in packs.items():
        nums = fmt_numbers(pack.get("numbers", []))
        prob = pack.get("theoretical_probability")
        prob_text = f"{prob * 100:.2f}%" if isinstance(prob, (int, float)) else "-"
        lines.append(f"- {pack.get('name')}：{nums} / 研究預測 / 目標 {pack.get('hit_goal')} / 理論機率 {prob_text} / 平均分 {pack.get('avg_score', '-')}")
    lines.extend(["", "## 建議組合"])
    for item in analysis["suggested_sets"]:
        lines.append(f"- {item['name']}：{fmt_numbers(item['numbers'])} / 特別號 {item['special']:02d}")
    lines.extend(["", "## Top 18 主號候選"])
    lines.append(", ".join(f"{item['number']:02d}({item['confidence_index']})" for item in candidates[:18]))
    lines.extend(["", "## 特別號 Top 10"])
    lines.append(", ".join(f"{item['number']:02d}({item['confidence_index']})" for item in special_candidates[:10]))
    if review.get("has_review"):
        settled = review["last_settled"]
        lines.extend(
            [
                "",
                "## 上期命中檢討",
                f"- 上次預測：{settled['based_on_period']} -> {settled['actual_period']}",
                f"- 實際本號：{fmt_numbers(settled['actual_numbers'])} / 特別號 {settled['actual_special']:02d}",
                f"- Top6 / Top12 / Top18 命中：{settled['top6_hits']} / {settled['top12_hits']} / {settled['top18_hits']}",
                f"- 特別號 Top1 / Top3：{settled['special_top1_hit']} / {settled['special_top3_hit']}",
                f"- 判定：{review.get('severity')}",
            ]
        )
        for action in review.get("actions", []):
            lines.append(f"- 改善：{action}")
        lines.extend(["", "## 強牌檢討"])
        for key, pack_hit in (settled.get("strong_pack_hits") or {}).items():
            lines.append(f"- {pack_hit.get('name', key)}：命中 {pack_hit.get('hits')} / 目標 {pack_hit.get('hit_goal')} / {'達標' if pack_hit.get('passed') else '未達標'}")
    lines.extend(["", "## 雙軌模型對照（原始未調整對照滾動調整）"])
    lines.append("- 舊基礎綜合與v7滾動權重已在本戰報回測摘要與latest_analysis.json完整保存。")
    lines.extend(["", "## 原始模型未調整排名"])
    base_rank = (analysis.get("backtest", {}).get("strategies", {}) or {})
    for name, info in sorted(base_rank.items())[:9]:
        lines.append(f"- {name}：Top12平均 {info.get('top12_avg_hits', '-')} / edge {info.get('top12_edge_vs_random', '-')}")
    lines.extend(["", "## 近期逐期對照"])
    for item in [p for p in history.get("periods", []) if p.get("status") == "settled"][:5]:
        lines.append(f"- {item.get('actual_date')}：Top6/Top12/Top18 {item.get('top6_hits')}/{item.get('top12_hits')}/{item.get('top18_hits')} / 特別號Top3 {item.get('special_top3_hit')}")
    lines.extend(
        [
            "",
            "## 模型回測摘要",
            f"- 基礎模組回測期數：{bt.get('rounds', 0)}，近期校準期數：{recent_bt.get('rounds', 0)}",
            f"- 隨機 Top12 期望命中：約 {bt.get('random_expectation', {}).get(12, 0)} 顆",
            f"- 舊基礎綜合 Top12：{ensemble.get('top12_avg_hits', '-')}，對隨機差值 {ensemble.get('top12_edge_vs_random', '-')}",
            f"- v7最終權重 Top12：{adaptive_bt.get('top12_avg_hits', '-')}，對隨機差值 {adaptive_bt.get('top12_edge_vs_random', '-')}",
            f"- v7最終權重 Top18：{adaptive_bt.get('top18_avg_hits', '-')}，對隨機差值 {adaptive_bt.get('top18_edge_vs_random', '-')}",
            "",
            "## 強牌實戰統計",
            f"- 已累積預測紀錄：{history['total_periods']} 期，已結算 {history['settled_periods']} 期。",
            "- 強牌達標率由後續已結算期數滾動累積；每期結算後更新。",
            "",
            "## 模型滾動調整",
        ]
    )
    for name, item in sorted(calibration.items(), key=lambda pair: pair[1].get("edge_score", 0), reverse=True):
        lines.append(f"- {name}：{item.get('tier')} / edge_score {item.get('edge_score')} / 正向票 {item.get('positive_votes')} / multiplier {item.get('multiplier')}")
    lines.extend(
        [
            "",
            "## 低機率達標檢討",
            f"- 本期低機率避險包：5不中 {fmt_numbers(avoid5)} / 10不中 {fmt_numbers(avoid10)} / 15不中 {fmt_numbers(avoid15)}",
            "",
            "## 鐵律狀態",
        ]
    )
    for law in analysis["iron_laws"]:
        lines.append(f"- {law}")
    lines.extend(["", f"- 已累積預測紀錄：{history['total_periods']} 期，已結算 {history['settled_periods']} 期。"])
    return "\n".join(lines) + "\n"


def candidate_table(candidates: list[dict], limit: int = 18) -> str:
    rows = []
    for item in candidates[:limit]:
        rows.append(
            "<tr>"
            f"<td>{item['number']:02d}</td>"
            f"<td>{item['confidence_index']}</td>"
            f"<td>{item['omission']}</td>"
            f"<td>{esc('、'.join(item.get('reasons') or ['綜合排序']))}</td>"
            "</tr>"
        )
    return "".join(rows)


def render_html(analysis: dict, history: dict, markdown_text: str) -> str:
    latest = analysis["latest_draw"]
    freshness = analysis["data_freshness"]
    candidates = analysis["candidates"]
    special_candidates = analysis["special_candidates"]
    packs = analysis["strong_prediction_packs"]

    def pack_numbers(key: str) -> list[int]:
        return packs.get(key, {}).get("numbers") or []

    def info_rows(rows: list[tuple[str, str]]) -> str:
        return "".join(f"<tr><th>{esc(label)}</th><td>{value}</td></tr>" for label, value in rows)

    review = analysis.get("failure_review") or {}
    severity = review.get("severity", "normal")
    status_text = "失手回饋強化" if severity in {"critical", "warning"} else "穩定觀察"
    high_confidence = candidates[:6]
    avoid5 = [item["number"] for item in candidates[-5:]]
    avoid10 = [item["number"] for item in candidates[-10:]]
    avoid15 = [item["number"] for item in candidates[-15:]]
    calibration = (analysis.get("calibration") or {}).get("models") or {}
    decision_rows = info_rows(
        [
            ("作戰結論", esc(status_text)),
            ("明確獨支", fmt_numbers(pack_numbers("strong_single")) or "-"),
            ("明確2中1", fmt_numbers(pack_numbers("two_hit_one")) or "-"),
            ("明確3中1", fmt_numbers(pack_numbers("three_hit_one")) or "-"),
            ("明確5中2", fmt_numbers(pack_numbers("five_hit_two")) or "-"),
            ("明確9中3", fmt_numbers(pack_numbers("nine_hit_three")) or "-"),
            ("高機率信心牌", fmt_numbers([item["number"] for item in high_confidence])),
            ("防守避開", fmt_numbers(avoid10)),
            ("特別號核心", fmt_numbers([item["number"] for item in special_candidates[:3]])),
        ]
    )
    verification_rows = "".join(
        "<tr>"
        f"<td>{idx}</td>"
        f"<td><strong>{item['number']:02d}</strong></td>"
        f"<td>{esc(item.get('confidence_index'))}</td>"
        f"<td>{esc(item.get('omission'))}</td>"
        f"<td>{esc(item.get('zone'))}</td>"
        f"<td>{esc(item.get('tail'))}</td>"
        f"<td>{esc('、'.join(item.get('reasons') or ['綜合排序']))}</td>"
        "</tr>"
        for idx, item in enumerate(candidates[:9], 1)
    )
    short_pack_rows = "".join(
        "<tr>"
        f"<td>{esc(label)}</td>"
        f"<td>{fmt_numbers(pack_numbers(key)) or '-'}</td>"
        f"<td>{esc((packs.get(key) or {}).get('hit_goal', '-'))}</td>"
        f"<td>{float((packs.get(key) or {}).get('theoretical_probability', 0))*100:.2f}%</td>"
        f"<td>{esc((packs.get(key) or {}).get('avg_score', '-'))}</td>"
        "</tr>"
        for key, label in [("strong_single", "獨支1中1"), ("two_hit_one", "2中1"), ("three_hit_one", "3中1")]
    )
    avoid_rows = "".join(
        f"<tr><td>{esc(label)}</td><td>{fmt_numbers(nums)}</td><td>依目前模型低分排序，僅作風控觀察</td></tr>"
        for label, nums in [("5不中", avoid5), ("10不中", avoid10), ("15不中", avoid15)]
    )
    schedule_rows = "".join(
        f"<tr><td>{esc(item[0])}</td><td>{esc(item[1])}</td><td>{esc(item[2])}</td></tr>"
        for item in [
            ("每日雲端掃描", "台灣時間 08:30", "自動更新資料、重算、檢測、同步手機版"),
            ("開獎後追加", "週二、週五 22:20 / 23:10", "追官方最新資料，避免隔天沿用舊報表"),
            ("失敗自修復", "每次雲端工作內", "latest失敗或檢測失敗時改跑全歷史全量重建"),
            ("禁止沿用", "每期輸出前", "未重新運算與未通過檢測不發布"),
        ]
    )
    rolling_rows = (
        "".join(
            "<tr>"
            f"<td>{esc(name)}</td>"
            f"<td>{esc(item.get('tier'))}</td>"
            f"<td>{esc(item.get('edge_score'))}</td>"
            f"<td>{esc(item.get('positive_votes'))}</td>"
            f"<td>{esc(item.get('multiplier'))}</td>"
            "</tr>"
            for name, item in sorted(calibration.items(), key=lambda pair: pair[1].get("edge_score", 0), reverse=True)
        )
        or "<tr><td colspan=\"5\">目前無校準資料</td></tr>"
    )
    pack_cards = "".join(
        f"""
        <article class="card">
          <h3>{esc(pack.get('name'))}</h3>
          <p class="nums">{fmt_numbers(pack.get('numbers', []))}</p>
          <p>目標 {esc(pack.get('hit_goal'))} / 理論機率 {float(pack.get('theoretical_probability', 0))*100:.2f}%</p>
        </article>
        """
        for pack in packs.values()
    )
    set_rows = "".join(
        f"<tr><td>{esc(item['name'])}</td><td>{fmt_numbers(item['numbers'])}</td><td>{item['special']:02d}</td></tr>"
        for item in analysis["suggested_sets"]
    )
    if review.get("has_review"):
        settled = review["last_settled"]
        pack_review_rows = "".join(
            "<tr>"
            f"<td>{esc(pack_hit.get('name', key))}</td>"
            f"<td>{esc(pack_hit.get('hits'))}</td>"
            f"<td>{esc(pack_hit.get('hit_goal'))}</td>"
            f"<td>{'達標' if pack_hit.get('passed') else '未達標'}</td>"
            "</tr>"
            for key, pack_hit in (settled.get("strong_pack_hits") or {}).items()
        )
        review_html = f"""
        <section class="band">
          <h2>上期命中檢討</h2>
          <p>上次預測 {esc(settled['based_on_period'])} -> 實際 {esc(settled['actual_period'])}</p>
          <p class="nums">{fmt_numbers(settled['actual_numbers'])} / 特別號 {settled['actual_special']:02d}</p>
          <p>Top6 / Top12 / Top18：{esc(settled['top6_hits'])} / {esc(settled['top12_hits'])} / {esc(settled['top18_hits'])}</p>
          <p>特別號 Top1 / Top3：{esc(settled['special_top1_hit'])} / {esc(settled['special_top3_hit'])}</p>
          <table><thead><tr><th>強牌</th><th>命中</th><th>目標</th><th>判定</th></tr></thead><tbody>{pack_review_rows}</tbody></table>
        </section>
        """
    else:
        review_html = "<section class=\"band\"><h2>上期命中檢討</h2><p>目前尚無已結算預測，首次建立後會從下一期開始累積。</p></section>"
    bt = analysis["backtest"]
    recent_bt = analysis.get("recent_backtest") or {}
    adaptive_bt = analysis.get("adaptive_backtest") or {}
    ensemble = bt.get("strategies", {}).get("ensemble", {})
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>台灣大樂透鐵律戰報</title>
  <style>
    :root {{
      color-scheme: light;
      --ink: #182027;
      --muted: #60717f;
      --line: #d7e1e8;
      --paper: #f6f8f5;
      --panel: #ffffff;
      --accent: #0e7c7b;
      --gold: #b7791f;
      --red: #b8323c;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font-family: "Microsoft JhengHei", "Noto Sans TC", system-ui, sans-serif; background: var(--paper); color: var(--ink); line-height: 1.6; }}
    header {{ padding: 22px 16px 18px; background: #12343b; color: white; }}
    header .wrap, main {{ max-width: 1120px; margin: 0 auto; }}
    h1 {{ margin: 0 0 8px; font-size: clamp(26px, 4vw, 42px); letter-spacing: 0; }}
    h2 {{ margin: 0 0 12px; font-size: 22px; }}
    h3 {{ margin: 0 0 8px; font-size: 16px; }}
    p {{ margin: 0 0 8px; }}
    .meta {{ color: #d7eef1; }}
    main {{ padding: 16px; }}
    .band {{ background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 16px; margin: 0 0 14px; }}
    .hero {{ display: grid; grid-template-columns: 1.3fr 0.7fr; gap: 14px; }}
    .nums {{ font-size: 24px; font-weight: 800; color: var(--accent); word-spacing: 8px; }}
    .special {{ color: var(--red); font-weight: 800; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 10px; }}
    .card {{ border: 1px solid var(--line); border-radius: 8px; padding: 12px; background: #fbfdfd; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 15px; }}
    th, td {{ padding: 9px 8px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; }}
    th {{ color: var(--muted); font-weight: 700; }}
    .pill {{ display: inline-block; padding: 3px 8px; border-radius: 999px; background: #e7f4f2; color: #075d5b; font-weight: 700; }}
    .warn {{ color: var(--gold); font-weight: 800; }}
    nav {{ display: flex; gap: 8px; flex-wrap: wrap; margin-top: 12px; }}
    nav a {{ color: white; text-decoration: none; border: 1px solid rgba(255,255,255,.35); border-radius: 8px; padding: 7px 10px; }}
    pre {{ white-space: pre-wrap; overflow-wrap: anywhere; background: #0f1f24; color: #eef7f6; padding: 14px; border-radius: 8px; }}
    @media (max-width: 760px) {{
      .hero {{ grid-template-columns: 1fr; }}
      .nums {{ font-size: 21px; word-spacing: 5px; }}
      table {{ font-size: 14px; }}
      th:nth-child(4), td:nth-child(4) {{ display: none; }}
    }}
  </style>
</head>
<body>
  <header>
    <div class="wrap">
      <h1>台灣大樂透鐵律戰報</h1>
      <p class="meta">產生時間 {esc(analysis['generated_at'])} / 全歷史 {esc(analysis['history_info']['draw_count'])} 期 / 手機雲端獨立版可用</p>
      <nav>
        <a href="#prediction">核心決策</a>
        <a href="#verify">逐號驗算</a>
        <a href="#review">上期檢討</a>
        <a href="#schedule">每日更新</a>
        <a href="#history">資料庫</a>
      </nav>
    </div>
  </header>
  <main>
    <section class="band hero" id="prediction">
      <div>
        <h2>本期明確作戰答案</h2>
        <p>以第 {esc(latest['period'])} 期 {esc(latest['draw_date'])} 開獎後全歷史資料庫重算。</p>
        <p>目標第 <strong>{esc(analysis['target_period'])}</strong> 期，預估開獎日 <strong>{esc(analysis['target_date'])}</strong></p>
        <p class="nums">{fmt_numbers([item['number'] for item in analysis['candidates'][:6]])} <span class="special">+ {analysis['special_candidates'][0]['number']:02d}</span></p>
      </div>
      <div>
        <h2>最新開獎</h2>
        <p class="nums">{fmt_numbers(latest['numbers'])} <span class="special">+ {latest['special']:02d}</span></p>
        <p><span class="pill">{esc(freshness.get('status'))}</span> 應有最新日 {esc(freshness.get('expected_latest_date'))}，落後 {esc(freshness.get('lag_days'))} 天</p>
      </div>
    </section>

    <section class="band">
      <h2>核心決策</h2>
      <table><tbody>{decision_rows}</tbody></table>
    </section>

    <section class="band">
      <h2>最強獨隻1中1</h2>
      <p class="nums">{fmt_numbers(pack_numbers("strong_single")) or "-"}</p>
      <p>高信心加註：{fmt_numbers([item['number'] for item in candidates[:3]])}</p>
    </section>

    <section class="band">
      <h2>高機率信心牌特別強調</h2>
      <table><thead><tr><th>號碼</th><th>信心</th><th>遺漏</th><th>理由</th></tr></thead><tbody>{candidate_table(high_confidence, 6)}</tbody></table>
    </section>

    <section class="band" id="verify">
      <h2>逐號多重驗算明細</h2>
      <table><thead><tr><th>排名</th><th>號碼</th><th>信心</th><th>遺漏</th><th>區間</th><th>尾</th><th>來源</th></tr></thead><tbody>{verification_rows}</tbody></table>
    </section>

    <section class="band">
      <h2>獨支 / 2中1 / 3中1 短包超強信心精算</h2>
      <table><thead><tr><th>包別</th><th>號碼</th><th>目標</th><th>理論機率</th><th>平均分</th></tr></thead><tbody>{short_pack_rows}</tbody></table>
    </section>

    <section class="band">
      <h2>低機率避險包</h2>
      <table><thead><tr><th>包別</th><th>號碼</th><th>說明</th></tr></thead><tbody>{avoid_rows}</tbody></table>
    </section>

    <section class="band" id="schedule">
      <h2>每日更新鐵律時間表</h2>
      <table><thead><tr><th>項目</th><th>時間</th><th>處理</th></tr></thead><tbody>{schedule_rows}</tbody></table>
    </section>

    <section class="band">
      <h2>強牌組精算</h2>
      <div class="grid">{pack_cards}</div>
    </section>

    <section class="band">
      <h2>建議組合</h2>
      <table><thead><tr><th>組合</th><th>本號</th><th>特別號</th></tr></thead><tbody>{set_rows}</tbody></table>
    </section>

    <section class="band">
      <h2>主號 Top 18</h2>
      <table><thead><tr><th>號碼</th><th>信心</th><th>遺漏</th><th>理由</th></tr></thead><tbody>{candidate_table(analysis['candidates'], 18)}</tbody></table>
    </section>

    <section class="band">
      <h2>特別號 Top 10</h2>
      <table><thead><tr><th>號碼</th><th>信心</th><th>遺漏</th><th>理由</th></tr></thead><tbody>{candidate_table(analysis['special_candidates'], 10)}</tbody></table>
    </section>

    <div id="review">{review_html}</div>

    <section class="band">
      <h2>模型回測摘要</h2>
      <p>基礎模組回測期數：{esc(bt.get('rounds', 0))} / 近期校準期數：{esc(recent_bt.get('rounds', 0))}</p>
      <p>隨機 Top12 期望命中：約 {esc(bt.get('random_expectation', {}).get(12, 0))} 顆</p>
      <p>舊基礎綜合 Top12：{esc(ensemble.get('top12_avg_hits', '-'))}，對隨機差值 {esc(ensemble.get('top12_edge_vs_random', '-'))}</p>
      <p>v7最終權重 Top12：{esc(adaptive_bt.get('top12_avg_hits', '-'))}，對隨機差值 {esc(adaptive_bt.get('top12_edge_vs_random', '-'))}</p>
      <p>v7最終權重 Top18：{esc(adaptive_bt.get('top18_avg_hits', '-'))}，對隨機差值 {esc(adaptive_bt.get('top18_edge_vs_random', '-'))}</p>
    </section>

    <section class="band">
      <h2>模型滾動調整</h2>
      <table><thead><tr><th>模組</th><th>狀態</th><th>Edge</th><th>正向票</th><th>倍率</th></tr></thead><tbody>{rolling_rows}</tbody></table>
    </section>

    <section class="band">
      <h2>低機率達標檢討</h2>
      <p>本期低機率避險包已輸出 5不中、10不中、15不中，後續結算會同步檢討是否達標。</p>
    </section>

    <section class="band" id="history">
      <h2>全歷史資料庫</h2>
      <p>{esc(analysis['history_info']['first_date'])} 至 {esc(analysis['history_info']['latest_date'])}，共 {esc(analysis['history_info']['draw_count'])} 期。</p>
      <p>資料來源：台灣彩券官方年度下載 API + 大樂透月查詢 API。</p>
      <p>預測紀錄：{esc(history['total_periods'])} 期，已結算 {esc(history['settled_periods'])} 期。</p>
    </section>

    <section class="band">
      <h2>原始 Markdown 戰報</h2>
      <pre>{esc(markdown_text)}</pre>
    </section>
  </main>
</body>
</html>
"""


def save_reports(conn: sqlite3.Connection, analysis: dict) -> dict:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    history = prediction_history(conn)
    markdown_text = render_markdown(analysis, history)
    html_text = render_html(analysis, history, markdown_text)
    ANALYSIS_JSON.write_text(json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8")
    HEALTH_JSON.write_text(json.dumps(analysis["history_info"], ensure_ascii=False, indent=2), encoding="utf-8")
    HISTORY_JSON.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
    BATTLE_MD.write_text(markdown_text, encoding="utf-8")
    BATTLE_HTML.write_text(html_text, encoding="utf-8")
    ENHANCED_BATTLE_HTML.write_text(html_text, encoding="utf-8")
    return {"history": history, "markdown": markdown_text, "html": html_text}


def build_mobile_cloud_site(analysis: dict, reports: dict) -> None:
    MOBILE_DIR.mkdir(parents=True, exist_ok=True)
    for source, target in [
        (ANALYSIS_JSON, MOBILE_DIR / "latest_analysis.json"),
        (BATTLE_MD, MOBILE_DIR / "latest_battle_report.md"),
        (BATTLE_HTML, MOBILE_DIR / "latest_battle_report.html"),
        (HISTORY_JSON, MOBILE_DIR / "prediction_history.json"),
        (HEALTH_JSON, MOBILE_DIR / "system_health.json"),
    ]:
        shutil.copy2(source, target)
    (MOBILE_DIR / "index.html").write_text(reports["html"], encoding="utf-8")
    (MOBILE_DIR / "offline.html").write_text(
        """<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>離線</title><body style="font-family:system-ui,'Microsoft JhengHei',sans-serif;padding:24px"><h1>大樂透鐵律戰報</h1><p>目前離線，請稍後再重新整理。</p></body></html>""",
        encoding="utf-8",
    )
    (MOBILE_DIR / "manifest.webmanifest").write_text(
        json.dumps(
            {
                "name": "台灣大樂透鐵律戰報",
                "short_name": "大樂透鐵律",
                "start_url": "./index.html",
                "display": "standalone",
                "background_color": "#f6f8f5",
                "theme_color": "#12343b",
                "lang": "zh-Hant",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    cache_name = f"lotto649-ironlaw-{taipei_now().strftime('%Y%m%d%H%M%S')}"
    (MOBILE_DIR / "service-worker.js").write_text(
        f"""const CACHE_NAME = '{cache_name}';
const APP_SHELL = [
  './',
  './index.html',
  './offline.html',
  './latest_analysis.json',
  './latest_battle_report.html',
  './latest_battle_report.md',
  './prediction_history.json',
  './self_test_report.json',
  './system_health.json',
  './manifest.webmanifest'
];
self.addEventListener('install', event => {{
  event.waitUntil(caches.open(CACHE_NAME).then(cache => cache.addAll(APP_SHELL)));
  self.skipWaiting();
}});
self.addEventListener('activate', event => {{
  event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(key => key !== CACHE_NAME).map(key => caches.delete(key)))));
  self.clients.claim();
}});
self.addEventListener('fetch', event => {{
  const request = event.request;
  if (request.method !== 'GET') return;
  event.respondWith(fetch(request).then(response => {{
    const copy = response.clone();
    caches.open(CACHE_NAME).then(cache => cache.put(request, copy));
    return response;
  }}).catch(() => caches.match(request).then(cached => cached || caches.match('./offline.html'))));
}});
""",
        encoding="utf-8",
    )
    (MOBILE_DIR / "version.json").write_text(
        json.dumps(
            {
                "version": analysis["version"],
                "generated_at": analysis["generated_at"],
                "latest_period": analysis["latest_draw"]["period"],
                "latest_date": analysis["latest_draw"]["draw_date"],
                "target_period": analysis["target_period"],
                "target_date": analysis["target_date"],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    if PAGES_DIR.exists():
        shutil.rmtree(PAGES_DIR)
    shutil.copytree(MOBILE_DIR, PAGES_DIR)


def quality_gate(conn: sqlite3.Connection, analysis: dict, export_count: int, prediction_status: str) -> dict:
    draws = load_draws(conn)
    health = history_health(conn)
    backtest_result = analysis.get("backtest") or {}
    adaptive_backtest = analysis.get("adaptive_backtest") or {}
    strategies = backtest_result.get("strategies") or {}
    expected_strategies = set(DEFAULT_MODEL_WEIGHTS) | {"ensemble"}
    expected_packs = {
        "strong_single",
        "two_hit_one",
        "three_hit_one",
        "five_hit_two",
        "nine_hit_three",
        "special_single",
        "special_three_watch",
    }
    checks = []

    def add_check(name: str, passed: bool, detail: str) -> None:
        checks.append({"name": name, "passed": bool(passed), "detail": detail})

    candidate_numbers = [item.get("number") for item in analysis.get("candidates", [])]
    special_numbers = [item.get("number") for item in analysis.get("special_candidates", [])]
    suggested_sets = analysis.get("suggested_sets") or []
    strong_packs = analysis.get("strong_prediction_packs") or {}
    model_weights = analysis.get("model_weights") or {}
    calibration = analysis.get("calibration") or {}
    recent_backtest = analysis.get("recent_backtest") or {}

    add_check("database_minimum_history", len(draws) >= 2000, f"{len(draws)} draws loaded")
    add_check("database_export_complete", export_count == len(draws), f"csv={export_count}, db={len(draws)}")
    add_check("database_integrity", not health.get("invalid_rows") and not health.get("duplicate_dates"), "no invalid rows or duplicate dates")
    add_check("database_freshness", health.get("freshness", {}).get("status") == "fresh", json.dumps(health.get("freshness", {}), ensure_ascii=False))
    add_check("main_candidates_complete", len(candidate_numbers) == NUMBER_MAX and len(set(candidate_numbers)) == NUMBER_MAX, f"{len(candidate_numbers)} candidates")
    add_check("main_candidates_valid", all(isinstance(n, int) and 1 <= n <= NUMBER_MAX for n in candidate_numbers), "all main candidates are 1-49")
    add_check("special_candidates_complete", len(special_numbers) == NUMBER_MAX and len(set(special_numbers)) == NUMBER_MAX, f"{len(special_numbers)} candidates")
    add_check("special_candidates_valid", all(isinstance(n, int) and 1 <= n <= NUMBER_MAX for n in special_numbers), "all special candidates are 1-49")
    add_check("strategy_modules_present", expected_strategies.issubset(strategies.keys()), f"missing={sorted(expected_strategies - set(strategies.keys()))}")
    add_check("backtest_depth", int(backtest_result.get("rounds") or 0) >= 300, f"rounds={backtest_result.get('rounds')}")
    add_check("recent_backtest_depth", int(recent_backtest.get("rounds") or 0) >= 120, f"rounds={recent_backtest.get('rounds')}")
    add_check("adaptive_calibration_present", calibration.get("method") == "dual_window_edge_shrink_v3", str(calibration.get("method")))
    adaptive_edge12 = float(adaptive_backtest.get("top12_edge_vs_random") or -99)
    adaptive_edge18 = float(adaptive_backtest.get("top18_edge_vs_random") or -99)
    add_check("adaptive_ensemble_backtest", int(adaptive_backtest.get("rounds") or 0) >= 300 and adaptive_edge12 >= 0 and adaptive_edge18 >= 0, f"rounds={adaptive_backtest.get('rounds')}, top12_edge={adaptive_edge12}, top18_edge={adaptive_edge18}")
    add_check("weights_complete", set(model_weights) == set(DEFAULT_MODEL_WEIGHTS), f"weights={sorted(model_weights.keys())}")
    add_check("weights_normalized", 0.98 <= sum(float(v) for v in model_weights.values()) <= 1.02, f"sum={sum(float(v) for v in model_weights.values()):.4f}")
    add_check("strong_packs_complete", expected_packs.issubset(strong_packs.keys()), f"missing={sorted(expected_packs - set(strong_packs.keys()))}")
    add_check("suggested_sets_count", len(suggested_sets) >= 6, f"{len(suggested_sets)} sets")
    valid_sets = True
    for item in suggested_sets:
        numbers = item.get("numbers") or []
        special = item.get("special")
        if len(numbers) != MAIN_DRAW_SIZE or len(set(numbers)) != MAIN_DRAW_SIZE:
            valid_sets = False
        if not all(isinstance(n, int) and 1 <= n <= NUMBER_MAX for n in numbers):
            valid_sets = False
        if not isinstance(special, int) or special < 1 or special > NUMBER_MAX or special in numbers:
            valid_sets = False
    add_check("suggested_sets_valid", valid_sets, "6 unique main numbers plus independent special number")
    add_check("prediction_saved", prediction_status in {"inserted", "updated_pending", "preserved_settled"}, prediction_status)
    workflow_path = BASE_DIR / ".github" / "workflows" / "update-mobile-cloud.yml"
    workflow_text = workflow_path.read_text(encoding="utf-8") if workflow_path.exists() else ""
    add_check(
        "daily_cloud_self_heal_workflow",
        "30 0 * * *" in workflow_text and "self-heal" in workflow_text.lower() and "--all" in workflow_text and "self_test_report.json" in workflow_text,
        "daily schedule, repair rebuild, and self-test verification present",
    )

    required_files = [
        CSV_PATH,
        ANALYSIS_JSON,
        HEALTH_JSON,
        HISTORY_JSON,
        BATTLE_MD,
        BATTLE_HTML,
        ENHANCED_BATTLE_HTML,
        MOBILE_DIR / "index.html",
        MOBILE_DIR / "manifest.webmanifest",
        MOBILE_DIR / "service-worker.js",
        PAGES_DIR / "index.html",
        PAGES_DIR / "latest_analysis.json",
        PAGES_DIR / "latest_battle_report.html",
        PAGES_DIR / "latest_battle_report.md",
        PAGES_DIR / "service-worker.js",
        MOBILE_DIR / "latest_battle_report.html",
        MOBILE_DIR / "latest_battle_report.md",
        workflow_path,
        BASE_DIR / "README.md",
    ]
    missing_files = [str(path.relative_to(BASE_DIR)) for path in required_files if not path.exists() or path.stat().st_size == 0]
    add_check("report_artifacts_present", not missing_files, f"missing={missing_files}")
    md_spec_markers = [
        "## 核心決策",
        "## 最強獨隻1中1",
        "## 高機率信心牌特別強調",
        "## 逐號多重驗算明細",
        "## 獨支 / 2中1 / 3中1 短包超強信心精算",
        "## 低機率避險包",
        "## 每日更新鐵律時間表",
        "## 強牌組精算",
        "## 上期命中檢討",
        "## 模型滾動調整",
    ]
    html_spec_markers = [marker.replace("## ", "<h2>") + "</h2>" for marker in md_spec_markers]
    md_paths = [BATTLE_MD, MOBILE_DIR / "latest_battle_report.md", PAGES_DIR / "latest_battle_report.md"]
    html_paths = [BATTLE_HTML, MOBILE_DIR / "index.html", PAGES_DIR / "index.html"]
    md_texts = [path.read_text(encoding="utf-8") if path.exists() else "" for path in md_paths]
    html_texts = [path.read_text(encoding="utf-8") if path.exists() else "" for path in html_paths]
    missing_spec = [marker for marker in md_spec_markers if not all(marker in text for text in md_texts)]
    missing_spec.extend(marker for marker in html_spec_markers if not all(marker in text for text in html_texts))
    add_check("battle_report_539_spec", not missing_spec, f"missing={missing_spec}")

    def file_digest(path: Path) -> str:
        if not path.exists() or path.stat().st_size == 0:
            return "missing"
        return hashlib.sha256(path.read_bytes()).hexdigest()

    sync_pairs = [
        ("analysis reports->mobile", ANALYSIS_JSON, MOBILE_DIR / "latest_analysis.json"),
        ("analysis reports->pages", ANALYSIS_JSON, PAGES_DIR / "latest_analysis.json"),
        ("battle md reports->mobile", BATTLE_MD, MOBILE_DIR / "latest_battle_report.md"),
        ("battle md reports->pages", BATTLE_MD, PAGES_DIR / "latest_battle_report.md"),
        ("battle html reports->mobile", BATTLE_HTML, MOBILE_DIR / "latest_battle_report.html"),
        ("battle html reports->pages", BATTLE_HTML, PAGES_DIR / "latest_battle_report.html"),
        ("battle html reports->mobile index", BATTLE_HTML, MOBILE_DIR / "index.html"),
        ("battle html reports->pages index", BATTLE_HTML, PAGES_DIR / "index.html"),
        ("history reports->mobile", HISTORY_JSON, MOBILE_DIR / "prediction_history.json"),
        ("history reports->pages", HISTORY_JSON, PAGES_DIR / "prediction_history.json"),
        ("health reports->mobile", HEALTH_JSON, MOBILE_DIR / "system_health.json"),
        ("health reports->pages", HEALTH_JSON, PAGES_DIR / "system_health.json"),
        ("mobile index->pages index", MOBILE_DIR / "index.html", PAGES_DIR / "index.html"),
        ("mobile version->pages version", MOBILE_DIR / "version.json", PAGES_DIR / "version.json"),
        ("mobile manifest->pages manifest", MOBILE_DIR / "manifest.webmanifest", PAGES_DIR / "manifest.webmanifest"),
        ("mobile service worker->pages service worker", MOBILE_DIR / "service-worker.js", PAGES_DIR / "service-worker.js"),
        ("mobile offline->pages offline", MOBILE_DIR / "offline.html", PAGES_DIR / "offline.html"),
    ]
    sync_mismatches = [
        label
        for label, left, right in sync_pairs
        if file_digest(left) == "missing" or file_digest(right) == "missing" or file_digest(left) != file_digest(right)
    ]
    add_check("computer_mobile_pages_sync", not sync_mismatches, f"mismatches={sync_mismatches}")

    expected_version = {
        "version": analysis.get("version"),
        "latest_period": analysis.get("latest_draw", {}).get("period"),
        "latest_date": analysis.get("latest_draw", {}).get("draw_date"),
        "target_period": analysis.get("target_period"),
        "target_date": analysis.get("target_date"),
    }
    version_sources = {
        "mobile": MOBILE_DIR / "version.json",
        "pages": PAGES_DIR / "version.json",
    }
    version_mismatches = []
    for label, path in version_sources.items():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            version_mismatches.append(f"{label}: {exc}")
            continue
        for key, expected in expected_version.items():
            if payload.get(key) != expected:
                version_mismatches.append(f"{label}.{key}={payload.get(key)!r} expected {expected!r}")
    add_check("published_version_consistency", not version_mismatches, f"mismatches={version_mismatches}")

    report = {
        "system": analysis.get("system"),
        "version": analysis.get("version"),
        "generated_at": taipei_now().isoformat(timespec="seconds"),
        "latest_period": analysis.get("latest_draw", {}).get("period"),
        "latest_date": analysis.get("latest_draw", {}).get("draw_date"),
        "target_period": analysis.get("target_period"),
        "target_date": analysis.get("target_date"),
        "passed": all(item["passed"] for item in checks),
        "checks": checks,
    }
    SELF_TEST_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    for target_dir in [MOBILE_DIR, PAGES_DIR]:
        if target_dir.exists():
            shutil.copy2(SELF_TEST_JSON, target_dir / "self_test_report.json")
    self_test_sync_mismatches = [
        str((target_dir / "self_test_report.json").relative_to(BASE_DIR))
        for target_dir in [MOBILE_DIR, PAGES_DIR]
        if file_digest(SELF_TEST_JSON) != file_digest(target_dir / "self_test_report.json")
    ]
    if self_test_sync_mismatches:
        raise RuntimeError("自我檢測報告同步失敗：" + ", ".join(self_test_sync_mismatches))
    if not report["passed"]:
        failed = [item for item in checks if not item["passed"]]
        raise RuntimeError("自我檢測未通過：" + "; ".join(f"{item['name']}={item['detail']}" for item in failed[:5]))
    return report


def ensure_github_workflow() -> None:
    workflow_dir = BASE_DIR / ".github" / "workflows"
    workflow_dir.mkdir(parents=True, exist_ok=True)
    (workflow_dir / "update-mobile-cloud.yml").write_text(
        """name: Daily Lotto649 IronLaw Mobile Cloud Self-Heal

on:
  schedule:
    - cron: "30 0 * * *"
    - cron: "20 14 * * 2,5"
    - cron: "10 15 * * 2,5"
  workflow_dispatch:

permissions:
  contents: write

concurrency:
  group: lotto649-mobile-cloud
  cancel-in-progress: false

jobs:
  update:
    runs-on: ubuntu-latest
    timeout-minutes: 25
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Daily update, full scan, and self-heal
        shell: bash
        run: |
          set -e
          verify_self_test() {
            python - <<'PY'
          import json, pathlib, sys
          report = json.loads(pathlib.Path("reports/self_test_report.json").read_text(encoding="utf-8"))
          if not report.get("passed"):
              print(json.dumps(report, ensure_ascii=False, indent=2))
              sys.exit(1)
          print("self test passed", report.get("latest_date"), "->", report.get("target_date"))
          PY
          }

          if ! python lotto649_ironlaw_system.py --latest; then
            echo "latest update failed, running full rebuild"
            python lotto649_ironlaw_system.py --all
          fi

          if ! verify_self_test; then
            echo "self test failed, running full rebuild repair"
            python lotto649_ironlaw_system.py --all
            verify_self_test
          fi
      - name: Commit refreshed data
        run: |
          git config user.name "lotto649-ironlaw-bot"
          git config user.email "actions@github.com"
          git add data reports mobile_cloud docs .github/workflows/update-mobile-cloud.yml README.md lotto649_ironlaw_system.py
          git diff --cached --quiet || git commit -m "Update Lotto649 iron-law reports"
          git push
""",
        encoding="utf-8",
    )


def write_readme() -> None:
    (BASE_DIR / "README.md").write_text(
        """# 台灣大樂透鐵律預測系統

本版依照 539 戰報鐵律規格改為台灣大樂透專用：

- 先建立官方全歷史 SQLite 與 CSV 資料庫，再產生候選號碼。
- 大樂透採 6/49 + 特別號，資料表分開保存本號與特別號。
- 多窗口分析近 5、10、20、50、100 期與長期資料。
- 強牌分層：最強單支、2中1、3中1、5中2、9中3，另列特別號單支與 3 碼觀察。
- 每期會結算 Top6、Top12、Top18、建議組合、強牌組、特別號命中。
- v3 模式加入 520 期 + 180 期雙回測校準，並追加最終權重 520 期回測；負邊際模型會降權或隔離。
- v4 模式加入失手回饋：Top6低命中或特別號Top3失手會直接降權，不等下一次爆掉。
- v5 模式加入每日雲端全系統掃描：每天自動更新、檢測，失敗會改跑全量重建修復並同步手機版。
- v6 模式把大樂透戰報對齊 539 規格：核心決策、逐號驗算、短包強牌、低機率避險、每日更新鐵律、模型滾動調整完整輸出。
- v7 模式新增電腦版、手機版、GitHub Pages逐檔同步檢測；不同步或未更新會直接判定失敗。
- 升級版保留 Bayesian/Dirichlet 平滑、EWMA 快慢週期、Markov 轉移、gap hazard、卡方區間/尾數平衡與組合搜尋。
- 每次輸出前會跑自我檢測，檢測失敗就中止。
- 輸出本機戰報與 `mobile_cloud` 雲端手機獨立版。

## 一鍵更新

```powershell
python .\\lotto649_ironlaw_system.py --all
```

離線重算既有資料庫：

```powershell
python .\\lotto649_ironlaw_system.py --analyze-only
```

完成後會產生：

- `data/lotto649.sqlite`
- `data/lotto649.csv`
- `reports/latest_battle_report.html`
- `reports/latest_analysis.json`
- `reports/self_test_report.json`
- `mobile_cloud/index.html`
- `docs/index.html`

## 雲端手機獨立版

把本資料夾放到 GitHub repo 後，啟用 GitHub Pages 與 Actions，Pages 發布來源設為 `main` 分支的 `/docs`。`.github/workflows/update-mobile-cloud.yml` 會每天台灣時間 08:30 做全系統掃描；週二、週五開獎後 22:20 與 23:10 追加更新。流程會驗證 `self_test_report.json`，失敗會自動改跑全量重建修復，通過後才提交 `data`、`reports`、`mobile_cloud` 與 `docs`。手機只需要打開 GitHub Pages 網址，不需要透過家裡電腦。

## 重要提醒

本系統是歷史統計與回測研究，不保證開獎命中或獲利，請量力而為。
""",
        encoding="utf-8",
    )


def run_update(args) -> dict:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    setup_logging()
    with sqlite3.connect(DB_PATH) as conn:
        init_db(conn)
        run_type = "analyze_only" if args.analyze_only else "all" if args.all else "latest"
        run_id = start_run(conn, run_type)
        try:
            if args.analyze_only:
                if not DB_PATH.exists():
                    raise RuntimeError("找不到既有資料庫，無法離線重算")
                message = "offline analysis rebuilt from existing database"
            elif args.all:
                backup_database()
                imported = import_all_years(conn)
                recent = import_recent_months(conn, 3)
                message = f"full history imported={imported}, recent={recent}"
            else:
                update_latest(conn)
                message = "latest updated"
            export_count = export_csv(conn)
            settled_count = settle_pending_predictions(conn)
            health = history_health(conn)
            if health.get("invalid_rows"):
                raise RuntimeError(f"資料完整性檢查未通過：{health['invalid_rows'][:3]}")
            analysis = analyze(conn)
            prediction_status = save_prediction(conn, analysis)
            analysis["official_prediction_status"] = prediction_status
            reports = save_reports(conn, analysis)
            build_mobile_cloud_site(analysis, reports)
            ensure_github_workflow()
            write_readme()
            self_test = quality_gate(conn, analysis, export_count, prediction_status)
            finish_run(
                conn,
                run_id,
                "success",
                f"{message}; csv={export_count}; settled={settled_count}; prediction={prediction_status}; self_test={self_test['passed']}",
            )
            return {
                "analysis": analysis,
                "history_health": health,
                "export_count": export_count,
                "settled_count": settled_count,
                "prediction_status": prediction_status,
                "self_test": self_test,
            }
        except Exception as exc:
            finish_run(conn, run_id, "failed", str(exc))
            raise


def parse_args(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(description="台灣大樂透鐵律預測系統")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--all", action="store_true", help="重建官方全歷史資料庫並產生戰報")
    group.add_argument("--latest", action="store_true", help="只更新最新資料並產生戰報")
    group.add_argument("--analyze-only", action="store_true", help="不連線，只用現有資料庫重算預測、戰報、手機雲端版與自我檢測")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.all and not args.latest and not args.analyze_only:
        args.all = True
    result = run_update(args)
    analysis = result["analysis"]
    print("台灣大樂透鐵律系統完成")
    print(f"全歷史資料：{analysis['history_info']['first_date']} 至 {analysis['history_info']['latest_date']}，共 {analysis['history_info']['draw_count']} 期")
    print(f"最新期別：{analysis['latest_draw']['period']} / 目標期別：{analysis['target_period']}")
    print(f"主號 Top6：{fmt_numbers([item['number'] for item in analysis['candidates'][:6]])}")
    print(f"特別號 Top3：{fmt_numbers([item['number'] for item in analysis['special_candidates'][:3]])}")
    print(f"自我檢測：{'通過' if result['self_test']['passed'] else '未通過'}")
    print(f"手機雲端版：{MOBILE_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
