from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from engine import ROOT

TAIPEI = ZoneInfo("Asia/Taipei")
DESTINATIONS = (ROOT / "reports", ROOT / "site", ROOT / "docs", ROOT / "mobile_cloud", ROOT / "docs" / "mobile_cloud")
STATUS_FILE = "self_repair_status.json"
STATE_PATHS = (ROOT / "data" / "official_lotto649.csv", ROOT / "data" / "prediction_history.json", *DESTINATIONS)
MAX_ATTEMPTS = 3
RETRY_SECONDS = 60


def expected_latest_date(now: datetime) -> str:
    day = now.date()
    if day.weekday() in (1, 4) and (now.hour, now.minute) < (22, 30):
        day -= timedelta(days=1)
    while day.weekday() not in (1, 4):
        day -= timedelta(days=1)
    return day.isoformat()


def write_status(status: str, attempt: int, detail: str) -> None:
    now = datetime.now(TAIPEI)
    payload = {
        "system": "台灣大樂透新世代鐵律預測系統",
        "status": status,
        "checked_at": now.isoformat(timespec="seconds"),
        "repair_deadline": "開獎後120分鐘（台灣時間22:30）",
        "attempt": attempt,
        "max_attempts": MAX_ATTEMPTS,
        "detail": detail,
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    for base in DESTINATIONS:
        base.mkdir(parents=True, exist_ok=True)
        (base / STATUS_FILE).write_text(text, encoding="utf-8")


def reports_are_fresh() -> tuple[bool, str]:
    analysis_path = ROOT / "reports" / "latest_analysis.json"
    if not analysis_path.exists():
        return False, "latest_analysis.json不存在"
    analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
    expected = expected_latest_date(datetime.now(TAIPEI))
    actual = analysis.get("latest_draw", {}).get("date", "")
    return actual >= expected, f"latest={actual}; expected>={expected}"


def run_once() -> tuple[bool, str]:
    update = subprocess.run([sys.executable, str(ROOT / "update.py")], cwd=ROOT)
    if update.returncode:
        return False, f"update.py失敗，代碼{update.returncode}"
    verify = subprocess.run([sys.executable, str(ROOT / "verify.py")], cwd=ROOT)
    if verify.returncode:
        return False, f"verify.py失敗，代碼{verify.returncode}"
    return reports_are_fresh()


def snapshot_state() -> Path:
    backup=Path(tempfile.mkdtemp(prefix="tw649-last-valid-"))
    for index,path in enumerate(STATE_PATHS):
        target=backup/str(index)
        if path.is_dir(): shutil.copytree(path,target)
        elif path.exists(): target.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(path,target)
    return backup


def restore_state(backup: Path) -> None:
    for index,path in enumerate(STATE_PATHS):
        source=backup/str(index)
        if not source.exists(): continue
        if path.is_dir(): shutil.rmtree(path); shutil.copytree(source,path)
        else: path.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(source,path)


def remove_snapshot(backup: Path) -> None:
    shutil.rmtree(backup,ignore_errors=True)


def main() -> int:
    for attempt in range(1, MAX_ATTEMPTS + 1):
        backup=snapshot_state()
        write_status("repairing", attempt, "抓取官方資料、全量重算與五處同步檢測")
        try:
            ok, detail = run_once()
        except Exception as exc:
            ok, detail = False, f"自主修復未預期錯誤: {exc}"
        if ok:
            remove_snapshot(backup)
            write_status("healthy", attempt, detail)
            print(json.dumps({"status": "healthy", "attempt": attempt, "detail": detail}, ensure_ascii=False))
            return 0
        restore_state(backup); remove_snapshot(backup)
        write_status("retrying" if attempt < MAX_ATTEMPTS else "failed", attempt, detail)
        if attempt < MAX_ATTEMPTS:
            time.sleep(RETRY_SECONDS)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
