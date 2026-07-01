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
    "omission": 0.92,
    "pair": 1.02,
    "tail_zone": 0.72,
    "repeat_neighbor": 0.52,
    "special_bridge": 0.44,
}

SPECIAL_MODEL_WEIGHTS = {
    "special_short": 1.12,
    "special_mid": 1.02,
    "special_long": 0.78,
    "special_omission": 0.92,
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
        "omission": normalize_map(all_omission),
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

    special_recent = frequency(recent100, field="special")
    special_bridge = {n: special_recent.get(n, 0) for n in range(1, NUMBER_MAX + 1)}
    components["special_bridge"] = normalize_map(special_bridge)

    for n in range(1, NUMBER_MAX + 1):
        if components["heat_short"][n] >= 0.75:
            reasons[n].append("近十期熱度高")
        if components["heat_mid"][n] >= 0.75:
            reasons[n].append("中期趨勢強")
        if components["omission"][n] >= 0.78:
            reasons[n].append("遺漏補償")
        if components["pair"][n] >= 0.72:
            reasons[n].append("上期關聯搭配")
        if components["tail_zone"][n] >= 0.68:
            reasons[n].append("尾數區間補位")
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
        "special_omission": normalize_map(all_omission),
        "main_bridge": normalize_map({n: main_recent.get(n, 0) for n in range(1, NUMBER_MAX + 1)}),
    }
    for n in range(1, NUMBER_MAX + 1):
        if components["special_short"][n] >= 0.75:
            reasons[n].append("特別號短線熱")
        if components["special_omission"][n] >= 0.78:
            reasons[n].append("特別號遺漏補償")
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
    if settled["top12_hits"] == 0 and not settled["special_top3_hit"]:
        severity = "critical"
        actions = [
            "降低短線熱號與上期關聯權重",
            "提高中期均衡、遺漏補償、尾數區間分散",
            "強牌組加入區間分散限制，避免集中同一段趨勢",
        ]
    elif settled["top12_hits"] <= 1:
        severity = "warning"
        actions = [
            "小幅降低短線追熱",
            "提高中期趨勢與區間分散比重",
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
            "heat_short": 0.72,
            "pair": 0.76,
            "repeat_neighbor": 0.72,
            "heat_mid": 1.16,
            "omission": 1.18,
            "tail_zone": 1.18,
            "heat_long": 1.04,
            "special_bridge": 0.90,
        }
    elif review.get("severity") == "warning":
        multipliers = {
            "heat_short": 0.90,
            "pair": 0.92,
            "heat_mid": 1.08,
            "tail_zone": 1.06,
            "omission": 1.06,
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
    long_freq = normalize_map({n: frequency(draws[-360:]).get(n, 0) for n in range(1, NUMBER_MAX + 1)})
    for n in range(1, NUMBER_MAX + 1):
        score[n] = score[n] * 0.94 + long_freq[n] * 0.06
    if review and review.get("severity") == "critical":
        settled = review.get("last_settled", {})
        actual_numbers = set(settled.get("actual_numbers") or [])
        failed_numbers = set((settled.get("candidate_numbers") or [])[:12]) - actual_numbers
        for n in failed_numbers:
            score[n] *= 0.45
            reasons[n].append("上期低命中懲罰")

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


def score_special_numbers(draws: list[dict]) -> list[dict]:
    components, all_omission, reasons = special_component_scores(draws)
    total_weight = sum(SPECIAL_MODEL_WEIGHTS.values()) or 1
    score = defaultdict(float)
    for name, values in components.items():
        weight = SPECIAL_MODEL_WEIGHTS.get(name, 0) / total_weight
        for n in range(1, NUMBER_MAX + 1):
            score[n] += weight * values.get(n, 0)
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


def calibrated_weights(backtest_result: dict) -> dict[str, float]:
    strategies = backtest_result.get("strategies", {})
    weights = {}
    for name in DEFAULT_MODEL_WEIGHTS:
        edge = strategies.get(name, {}).get("top12_edge_vs_random", 0)
        weights[name] = DEFAULT_MODEL_WEIGHTS[name] * (1 + max(min(edge, 0.30), -0.22))
    total = sum(weights.values()) or 1
    return {name: round(value / total, 4) for name, value in weights.items()}


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


def build_sets(candidates: list[dict], special_candidates: list[dict]) -> list[dict]:
    top = [item["number"] for item in candidates[:24]]
    overdue = [item["number"] for item in sorted(candidates, key=lambda x: (x["omission"], x["score"]), reverse=True)[:12]]
    templates = [
        [top[0], top[2], top[5], top[8], top[11], top[14]],
        [top[1], top[3], top[6], top[9], overdue[0], overdue[2]],
        [top[0], top[4], top[7], top[10], top[15], overdue[1]],
        [top[2], top[5], top[12], top[16], overdue[3], overdue[4]],
        [top[1], top[8], top[13], top[17], overdue[5], overdue[6]],
        optimized_group(candidates, 6),
    ]
    sets = []
    seen = set()
    for idx, numbers in enumerate(templates, start=1):
        numbers = sorted(set(numbers))
        if len(numbers) < 6:
            for n in top:
                if n not in numbers:
                    numbers.append(n)
                if len(numbers) == 6:
                    break
        numbers = sorted(numbers[:6])
        key = tuple(numbers)
        if key in seen:
            continue
        seen.add(key)
        special = next(item["number"] for item in special_candidates if item["number"] not in numbers)
        sets.append({"name": f"建議組合{idx}", "numbers": numbers, "special": special})
    return sets


def analyze(conn: sqlite3.Connection) -> dict:
    draws = load_draws(conn)
    if len(draws) < 150:
        raise RuntimeError("大樂透歷史資料不足，禁止產生正式預測")
    latest = draws[-1]
    review = failure_review(conn)
    initial_backtest = backtest(draws)
    weights = apply_failure_adjustment(calibrated_weights(initial_backtest), review)
    candidates = score_numbers(draws, weights, review)
    special_candidates = score_special_numbers(draws)
    strong_packs = build_strong_prediction_packs(candidates, special_candidates)
    suggested_sets = build_sets(candidates, special_candidates)
    health = history_health(conn)
    target_date = next_draw_date(latest["draw_date"])
    return {
        "system": "台灣大樂透鐵律預測系統",
        "version": "lotto649_ironlaw_cloud_v1_20260701",
        "generated_at": taipei_now().isoformat(timespec="seconds"),
        "history_info": health,
        "latest_draw": latest,
        "target_period": latest["period"] + 1,
        "target_date": target_date,
        "data_freshness": health.get("freshness", {}),
        "failure_review": review,
        "backtest": initial_backtest,
        "model_weights": weights,
        "candidates": candidates,
        "special_candidates": special_candidates,
        "suggested_sets": suggested_sets,
        "strong_prediction_packs": strong_packs,
        "iron_laws": [
            "資料先行：先建立 SQLite 與 CSV 全歷史資料庫，再產生候選號碼",
            "多窗口分析：近5、10、20、50、100期與長期資料同步評分",
            "強牌分層：單支、2中1、3中1、5中2、9中3，另列特別號層",
            "上一期必結算：保留 Top6、Top12、Top18、強牌組與特別號命中",
            "交叉驗證：年度官方檔與月查詢 API 互補",
            "戰報透明：輸出 latest_analysis.json、HTML、Markdown 與手機雲端版",
            "不迷信單一模型：熱度、冷度、遺漏、關聯、尾數區間與回測共同決策",
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
    lines = [
        "# 台灣大樂透鐵律戰報",
        "",
        f"- 產生時間：{analysis['generated_at']}",
        f"- 全歷史資料庫：{analysis['history_info']['first_date']} 至 {analysis['history_info']['latest_date']}，共 {analysis['history_info']['draw_count']} 期",
        f"- 最新期別：{latest['period']} ({latest['draw_date']})",
        f"- 最新本號：{fmt_numbers(latest['numbers'])} / 特別號 {latest['special']:02d}",
        f"- 目標期別：{analysis['target_period']}，預估開獎日：{analysis['target_date']}",
        f"- 資料新鮮度：{freshness.get('status')}，應有最新日 {freshness.get('expected_latest_date')}，落後 {freshness.get('lag_days')} 天",
        "- 提醒：本戰報是歷史統計與回測研究，不保證開出或獲利。",
        "",
        "## 本期強牌分層",
    ]
    for pack in analysis["strong_prediction_packs"].values():
        nums = fmt_numbers(pack.get("numbers", []))
        prob = pack.get("theoretical_probability")
        prob_text = f"{prob * 100:.2f}%" if isinstance(prob, (int, float)) else "-"
        lines.append(f"- {pack.get('name')}：{nums}，目標 {pack.get('hit_goal')}，理論機率 {prob_text}")
    lines.extend(["", "## 建議組合"])
    for item in analysis["suggested_sets"]:
        lines.append(f"- {item['name']}：{fmt_numbers(item['numbers'])} / 特別號 {item['special']:02d}")
    lines.extend(["", "## Top 18 主號候選"])
    lines.append(", ".join(f"{item['number']:02d}({item['confidence_index']})" for item in analysis["candidates"][:18]))
    lines.extend(["", "## 特別號 Top 10"])
    lines.append(", ".join(f"{item['number']:02d}({item['confidence_index']})" for item in analysis["special_candidates"][:10]))
    review = analysis.get("failure_review") or {}
    if review.get("has_review"):
        settled = review["last_settled"]
        lines.extend(
            [
                "",
                "## 上期結算檢討",
                f"- 上次預測：{settled['based_on_period']} -> {settled['actual_period']}",
                f"- 實際本號：{fmt_numbers(settled['actual_numbers'])} / 特別號 {settled['actual_special']:02d}",
                f"- Top6 / Top12 / Top18 命中：{settled['top6_hits']} / {settled['top12_hits']} / {settled['top18_hits']}",
                f"- 特別號 Top1 / Top3：{settled['special_top1_hit']} / {settled['special_top3_hit']}",
                f"- 判定：{review.get('severity')}",
            ]
        )
        for action in review.get("actions", []):
            lines.append(f"- 改善：{action}")
    bt = analysis["backtest"]
    ensemble = bt.get("strategies", {}).get("ensemble", {})
    lines.extend(
        [
            "",
            "## 模型回測",
            f"- 回測期數：{bt.get('rounds', 0)}",
            f"- 隨機 Top12 期望命中：約 {bt.get('random_expectation', {}).get(12, 0)} 顆",
            f"- 綜合模型 Top12 平均命中：{ensemble.get('top12_avg_hits', '-')}，對隨機差值 {ensemble.get('top12_edge_vs_random', '-')}",
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
    packs = analysis["strong_prediction_packs"]
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
    review = analysis.get("failure_review") or {}
    if review.get("has_review"):
        settled = review["last_settled"]
        review_html = f"""
        <section class="band">
          <h2>上期結算檢討</h2>
          <p>上次預測 {esc(settled['based_on_period'])} -> 實際 {esc(settled['actual_period'])}</p>
          <p class="nums">{fmt_numbers(settled['actual_numbers'])} / 特別號 {settled['actual_special']:02d}</p>
          <p>Top6 / Top12 / Top18：{esc(settled['top6_hits'])} / {esc(settled['top12_hits'])} / {esc(settled['top18_hits'])}</p>
          <p>特別號 Top1 / Top3：{esc(settled['special_top1_hit'])} / {esc(settled['special_top3_hit'])}</p>
        </section>
        """
    else:
        review_html = "<section class=\"band\"><h2>上期結算檢討</h2><p>目前尚無已結算預測，首次建立後會從下一期開始累積。</p></section>"
    bt = analysis["backtest"]
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
        <a href="#prediction">下期預測</a>
        <a href="#review">上期檢討</a>
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
      <h2>強牌分層</h2>
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
      <h2>模型回測</h2>
      <p>回測期數：{esc(bt.get('rounds', 0))}</p>
      <p>隨機 Top12 期望命中：約 {esc(bt.get('random_expectation', {}).get(12, 0))} 顆</p>
      <p>綜合模型 Top12 平均命中：{esc(ensemble.get('top12_avg_hits', '-'))}，對隨機差值 {esc(ensemble.get('top12_edge_vs_random', '-'))}</p>
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


def ensure_github_workflow() -> None:
    workflow_dir = BASE_DIR / ".github" / "workflows"
    workflow_dir.mkdir(parents=True, exist_ok=True)
    (workflow_dir / "update-mobile-cloud.yml").write_text(
        """name: Update Lotto649 IronLaw Mobile Cloud

on:
  schedule:
    - cron: "30 14 * * 2,5"
  workflow_dispatch:

permissions:
  contents: write

jobs:
  update:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Build database, prediction, reports, and mobile site
        run: python lotto649_ironlaw_system.py --all
      - name: Commit refreshed data
        run: |
          git config user.name "lotto649-ironlaw-bot"
          git config user.email "actions@github.com"
          git add data reports mobile_cloud docs
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
- 輸出本機戰報與 `mobile_cloud` 雲端手機獨立版。

## 一鍵更新

```powershell
python .\\lotto649_ironlaw_system.py --all
```

完成後會產生：

- `data/lotto649.sqlite`
- `data/lotto649.csv`
- `reports/latest_battle_report.html`
- `reports/latest_analysis.json`
- `mobile_cloud/index.html`
- `docs/index.html`

## 雲端手機獨立版

把本資料夾放到 GitHub repo 後，啟用 GitHub Pages 與 Actions，Pages 發布來源設為 `main` 分支的 `/docs`。`.github/workflows/update-mobile-cloud.yml` 會在台灣時間週二、週五晚間開獎後自動更新 `data`、`reports`、`mobile_cloud` 與 `docs`。手機只需要打開 GitHub Pages 網址，不需要透過家裡電腦。

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
        run_id = start_run(conn, "all" if args.all else "latest")
        try:
            if args.all:
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
            finish_run(
                conn,
                run_id,
                "success",
                f"{message}; csv={export_count}; settled={settled_count}; prediction={prediction_status}",
            )
            return {
                "analysis": analysis,
                "history_health": health,
                "export_count": export_count,
                "settled_count": settled_count,
                "prediction_status": prediction_status,
            }
        except Exception as exc:
            finish_run(conn, run_id, "failed", str(exc))
            raise


def parse_args(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(description="台灣大樂透鐵律預測系統")
    parser.add_argument("--all", action="store_true", help="重建官方全歷史資料庫並產生戰報")
    parser.add_argument("--latest", action="store_true", help="只更新最新資料並產生戰報")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.all and not args.latest:
        args.all = True
    result = run_update(args)
    analysis = result["analysis"]
    print("台灣大樂透鐵律系統完成")
    print(f"全歷史資料：{analysis['history_info']['first_date']} 至 {analysis['history_info']['latest_date']}，共 {analysis['history_info']['draw_count']} 期")
    print(f"最新期別：{analysis['latest_draw']['period']} / 目標期別：{analysis['target_period']}")
    print(f"主號 Top6：{fmt_numbers([item['number'] for item in analysis['candidates'][:6]])}")
    print(f"特別號 Top3：{fmt_numbers([item['number'] for item in analysis['special_candidates'][:3]])}")
    print(f"手機雲端版：{MOBILE_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
