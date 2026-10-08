from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parent

BASE_URL="https://pingshen670822.github.io/taiwan-lotto649-ironlaw-cloud/mobile_cloud"
OUTPUT=ROOT/"reports"/"live_cloud_verification.json"

def fetch(name: str):
    url=f"{BASE_URL}/{name}?t={int(time.time())}"
    with urlopen(Request(url,headers={"Cache-Control":"no-cache","Pragma":"no-cache"}),timeout=30) as response:
        data=response.read()
    return json.loads(data.decode("utf-8")) if name.endswith(".json") else data.decode("utf-8",errors="replace")

def expected_latest_date() -> str:
    now=datetime.now(ZoneInfo("Asia/Taipei")); day=now.date()
    if day.weekday() in (1,4) and (now.hour,now.minute)<(22,30): day-=timedelta(days=1)
    while day.weekday() not in (1,4): day-=timedelta(days=1)
    return day.isoformat()

def main() -> int:
    local_analysis=json.loads((ROOT/"docs"/"mobile_cloud"/"latest_analysis.json").read_text(encoding="utf-8"))
    local_version=json.loads((ROOT/"docs"/"mobile_cloud"/"version.json").read_text(encoding="utf-8"))
    checks=[]
    try:
        remote_analysis=fetch("latest_analysis.json"); remote_pattern=fetch("single_pattern_audit.json"); remote_version=fetch("version.json"); remote_test=fetch("self_test_report.json"); remote_repair=fetch("self_repair_status.json"); remote_html=fetch("latest_battle_report.html"); remote_app=fetch("app.js")
        checks=[
            {"name":"version_hash_matches","passed":remote_version.get("hash")==local_version.get("hash"),"detail":f"local={local_version.get('hash')} remote={remote_version.get('hash')}"},
            {"name":"latest_period_matches","passed":remote_analysis.get("latest_draw",{}).get("period")==local_analysis.get("latest_draw",{}).get("period"),"detail":f"local={local_analysis.get('latest_draw',{}).get('period')} remote={remote_analysis.get('latest_draw',{}).get('period')}"},
            {"name":"remote_latest_draw_fresh","passed":remote_analysis.get("latest_draw",{}).get("date","")>=expected_latest_date(),"detail":f"remote={remote_analysis.get('latest_draw',{}).get('date')} expected>={expected_latest_date()}"},
            {"name":"remote_daily_prediction_refresh","passed":str(remote_analysis.get("generated_at",""))[:10]==datetime.now(ZoneInfo("Asia/Taipei")).date().isoformat(),"detail":f"generated={remote_analysis.get('generated_at')}; today={datetime.now(ZoneInfo('Asia/Taipei')).date().isoformat()}"},
            {"name":"prediction_target_date_matches","passed":remote_analysis.get("target_date")==local_analysis.get("target_date"),"detail":f"local={local_analysis.get('target_date')} remote={remote_analysis.get('target_date')}"},
            {"name":"prediction_matches","passed":remote_analysis.get("packs")==local_analysis.get("packs"),"detail":"all prediction packs"},
            {"name":"strongest_single_date_metadata","passed":remote_analysis.get("strongest_recommendation",{}).get("label")=="終極獨隻・全系統最高順位" and remote_analysis.get("strongest_recommendation",{}).get("confidence_status") in ("完整通過","回測審慎級") and remote_analysis.get("strongest_recommendation",{}).get("target_draw_date")==remote_analysis.get("target_date") and remote_analysis.get("strongest_recommendation",{}).get("based_on_period")==remote_analysis.get("latest_draw",{}).get("period") and remote_analysis.get("strongest_recommendation",{}).get("data_cutoff_date")==remote_analysis.get("latest_draw",{}).get("date") and remote_analysis.get("strongest_recommendation",{}).get("calculated_at")==remote_analysis.get("generated_at"),"detail":json.dumps({key:remote_analysis.get("strongest_recommendation",{}).get(key) for key in ("label","confidence_status","target_draw_date","based_on_period","data_cutoff_date","calculated_at")},ensure_ascii=False)},
            {"name":"full_system_module_registry","passed":remote_analysis.get("full_system_module_audit",{}).get("registered")==remote_analysis.get("full_system_module_audit",{}).get("evaluated") and remote_analysis.get("full_system_module_audit",{}).get("registered",0)>=24 and remote_analysis.get("full_system_module_audit",{}).get("selected_number")==remote_analysis.get("strongest_recommendation",{}).get("number"),"detail":json.dumps({key:remote_analysis.get("full_system_module_audit",{}).get(key) for key in ("registered","evaluated","weighted","zero_weight","selected_number")},ensure_ascii=False)},
            {"name":"dual_window_pattern_audit","passed":remote_pattern.get("development_rounds")==520 and remote_pattern.get("holdout_rounds")==520 and len(remote_pattern.get("methods",{}))>=10 and remote_analysis.get("pattern_audit")==remote_pattern,"detail":f"methods={len(remote_pattern.get('methods',{}))}; accepted={remote_pattern.get('accepted_methods',[])}"},
            {"name":"remote_self_test_passed","passed":remote_test.get("passed") is True,"detail":remote_test.get("generated_at")},
            {"name":"remote_repair_status_valid","passed":remote_repair.get("status") in ("healthy","awaiting_verification") and remote_repair.get("latest_period")==remote_version.get("latest_period"),"detail":f'{remote_repair.get("status")} @ {remote_repair.get("checked_at")}'},
            {"name":"mobile_no_store_and_refresh","passed":"no-cache, no-store" in remote_html and "refreshVersion" in remote_app and "visibilitychange" in remote_app,"detail":"HTML cache and foreground refresh"},
            {"name":"manual_update_and_repair_controls","passed":all(x in remote_html for x in ('id=\"manual-refresh\"','id=\"emergency-repair\"','id=\"cloud-action-status\"')) and all(x in remote_app for x in ('manualUpdateLatest','immediateRepair','clearBrokenClientState','validateCloudBundle','attempt<=3','pageHash','version.hash!==pageHash','目前已是最新，不需重新載入')),"detail":"manual update visibly reports current/new version, three-pass repair, and truthful validation"},
            {"name":"marksix_interface_and_visible_times","passed":all(x in remote_html for x in ('台灣大樂透・本期戰報','data-tab=\"decision\"','data-tab=\"models\"','data-tab=\"review\"','data-tab=\"monthly\"','data-tab=\"verify\"','data-tab=\"iron\"','終極獨隻・全系統最高順位','終極獨隻 1中1','適用開獎日：','依據第','資料截止','運算時間','回測信心：','全系統模組','全系統模組逐項運算','軌跡、週期與拖牌雙區段稽核','本期其他鐵律號碼','last-manual-update','last-repair-time','data-manual-time','data-repair-time')) and all(x in remote_app for x in ('taipeiNow','tw649-manual-update-time','tw649-repair-time','showStoredTimes')),"detail":"Mark Six-style burgundy tabbed layout and persistent Taipei timestamps"},
        ]
        error=None
    except Exception as exc:
        error=str(exc); checks=[{"name":"cloud_reachable","passed":False,"detail":error}]
    report={"system":"台灣大樂透新世代鐵律預測系統","checked_at":datetime.now().astimezone().isoformat(timespec="seconds"),"base_url":BASE_URL,"passed":all(x["passed"] for x in checks),"checks":checks}
    OUTPUT.parent.mkdir(parents=True,exist_ok=True); OUTPUT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    output=json.dumps(report,ensure_ascii=False,indent=2)
    if hasattr(sys.stdout,"buffer"):
        sys.stdout.buffer.write(output.encode("utf-8")+b"\n")
    else:
        print(output)
    return 0 if report["passed"] else 1

if __name__=="__main__": sys.exit(main())
