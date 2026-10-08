from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parent
MARKER=ROOT/"docs"/".pages-repair-trigger.json"
MAX_ATTEMPTS=3
STATUS_PATHS=(
    ROOT/"reports"/"self_repair_status.json",
    ROOT/"site"/"self_repair_status.json",
    ROOT/"docs"/"self_repair_status.json",
    ROOT/"mobile_cloud"/"self_repair_status.json",
    ROOT/"docs"/"mobile_cloud"/"self_repair_status.json",
)

def write_healthy_status() -> None:
    source=STATUS_PATHS[0]
    status=json.loads(source.read_text(encoding="utf-8")) if source.exists() else {}
    if status.get("status")=="healthy":
        return
    version=json.loads((ROOT/"docs"/"mobile_cloud"/"version.json").read_text(encoding="utf-8"))
    status.update({
        "status":"healthy",
        "checked_at":datetime.now(ZoneInfo("Asia/Taipei")).isoformat(timespec="seconds"),
        "latest_period":version.get("latest_period"),
        "live_cloud_verification":"passed",
    })
    text=json.dumps(status,ensure_ascii=False,indent=2)
    for path in STATUS_PATHS:
        path.write_text(text,encoding="utf-8")

def main() -> int:
    result=subprocess.run([sys.executable,str(ROOT/"cloud_verify.py")],cwd=ROOT)
    if result.returncode==0:
        write_healthy_status()
        print("Pages與手機雲端同步正常，不需要重建。")
        return 0
    version=json.loads((ROOT/"docs"/"mobile_cloud"/"version.json").read_text(encoding="utf-8"))
    expected=version.get("hash")
    try: old=json.loads(MARKER.read_text(encoding="utf-8"))
    except (OSError,json.JSONDecodeError): old={}
    attempt=(int(old.get("attempt",0))+1) if old.get("expected_hash")==expected else 1
    if attempt>MAX_ATTEMPTS:
        print(f"Pages自主修復已達{MAX_ATTEMPTS}次上限，保留診斷供下一輪排程檢查。")
        return 1
    marker={"expected_hash":expected,"latest_period":version.get("latest_period"),"attempt":attempt,"max_attempts":MAX_ATTEMPTS,"triggered_at":datetime.now(timezone.utc).isoformat(timespec="seconds")}
    MARKER.write_text(json.dumps(marker,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(marker,ensure_ascii=False))
    return 0

if __name__=="__main__": raise SystemExit(main())
