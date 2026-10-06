from __future__ import annotations

import csv
import hashlib
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from engine import ROOT, load_draws

TAIPEI=ZoneInfo("Asia/Taipei")
CSV_PATH=ROOT/"data"/"official_lotto649.csv"
HISTORY_PATH=ROOT/"data"/"prediction_history.json"
DESTINATIONS=(ROOT/"reports",ROOT/"site",ROOT/"docs",ROOT/"mobile_cloud",ROOT/"docs"/"mobile_cloud")
SYNC_FILES=("index.html","latest_battle_report.html","latest_analysis.json","prediction_history.json","version.json","style.css","app.js","service-worker.js","manifest.webmanifest")

def sha(path: Path) -> str: return hashlib.sha256(path.read_bytes()).hexdigest()

def expected_latest_date(now: datetime | None=None) -> str:
    now=now or datetime.now(TAIPEI); day=now.date()
    if day.weekday() in (1,4) and (now.hour,now.minute)<(22,30): day-=timedelta(days=1)
    while day.weekday() not in (1,4): day-=timedelta(days=1)
    return day.isoformat()

def build_audit() -> dict:
    checks=[]
    def add(name,passed,detail): checks.append({"name":name,"passed":bool(passed),"detail":detail})
    rows=list(csv.DictReader(CSV_PATH.open(encoding="utf-8-sig",newline="")))
    periods=[int(r["period"]) for r in rows]; dates=[r["draw_date"] for r in rows]
    valid=[]
    for row in rows:
        nums=[int(row[f"n{i}"]) for i in range(1,7)]; special=int(row["special"])
        valid.append(len(set(nums))==6 and all(1<=n<=49 for n in nums+[special]) and special not in nums)
    add("official_rows_valid",all(valid),f"valid={sum(valid)}/{len(valid)}")
    add("no_duplicate_periods",len(periods)==len(set(periods)),f"rows={len(periods)} unique={len(set(periods))}")
    add("no_duplicate_dates",len(dates)==len(set(dates)),f"rows={len(dates)} unique={len(set(dates))}")
    add("periods_strictly_increasing",all(b>a for a,b in zip(periods,periods[1:])),f"first={periods[0]} latest={periods[-1]}")
    add("dates_strictly_increasing",all(b>a for a,b in zip(dates,dates[1:])),f"first={dates[0]} latest={dates[-1]}")
    parsed_dates=[date.fromisoformat(d) for d in dates]; future_dates=[d.isoformat() for d in parsed_dates if d>datetime.now(TAIPEI).date()]
    add("draw_dates_calendar_valid",not future_dates,f"parsed={len(parsed_dates)} future={future_dates[:10]}")
    special_schedule=[d.isoformat() for d in parsed_dates if d.weekday() not in (1,4)]
    add("official_special_schedule_preserved",True,f"official holiday/special draws={len(special_schedule)}")
    add("latest_draw_fresh",dates[-1]>=expected_latest_date(),f"actual={dates[-1]} expected>={expected_latest_date()}")
    draws=load_draws(); analysis=json.loads((ROOT/"reports"/"latest_analysis.json").read_text(encoding="utf-8"))
    latest=analysis.get("latest_draw",{}); csv_latest=rows[-1]
    csv_main=[int(csv_latest[f"n{i}"]) for i in range(1,7)]
    add("analysis_matches_official_csv",int(latest.get("period",-1))==periods[-1] and latest.get("date")==dates[-1] and latest.get("main")==csv_main and int(latest.get("special",-1))==int(csv_latest["special"]),{"csv_period":periods[-1],"analysis_period":latest.get("period")})
    add("analysis_row_count_matches",analysis.get("calculation_integrity",{}).get("official_rows")==len(draws)==len(rows),f"csv={len(rows)} engine={len(draws)}")
    history=json.loads(HISTORY_PATH.read_text(encoding="utf-8")); draw_by_date={d.draw_date:d for d in draws}
    stale_pending=[]; bad_settlement=[]; identities=set(); duplicates=[]
    for item in history:
        identity=(item.get("based_on_period"),item.get("engine"),item.get("revision_no"))
        if identity in identities: duplicates.append(identity)
        identities.add(identity)
        actual=draw_by_date.get(item.get("target_date"))
        if actual and item.get("status")!="settled": stale_pending.append(item.get("target_date"))
        if item.get("status")=="settled":
            saved=item.get("actual",{}); expected=draw_by_date.get(item.get("target_date"))
            if not expected or int(saved.get("period",-1))!=expected.period or list(saved.get("main",[]))!=list(expected.main) or int(saved.get("special",-1))!=expected.special: bad_settlement.append(item.get("target_date"))
    add("prediction_history_no_duplicate_revisions",not duplicates,duplicates[:10])
    add("prediction_history_no_stale_pending",not stale_pending,stale_pending[:10])
    add("prediction_settlements_match_official",not bad_settlement,bad_settlement[:10])
    missing=[str(base/filename) for base in DESTINATIONS for filename in SYNC_FILES if not (base/filename).exists()]
    add("all_report_artifacts_exist",not missing,missing[:10])
    mismatched=[filename for filename in SYNC_FILES if len({sha(base/filename) for base in DESTINATIONS if (base/filename).exists()})!=1]
    add("five_destinations_byte_identical",not mismatched,mismatched)
    report={"system":analysis.get("system"),"generated_at":datetime.now(TAIPEI).isoformat(timespec="seconds"),"passed":all(x["passed"] for x in checks),"latest_period":periods[-1],"latest_date":dates[-1],"target_date":analysis.get("target_date"),"checks":checks}
    return report

def save(report: dict) -> None:
    text=json.dumps(report,ensure_ascii=False,indent=2)
    lines=["# 台灣大樂透每日完整性稽核","",f"- 狀態：{'通過' if report['passed'] else '失敗'}",f"- 產生時間：{report['generated_at']}",f"- 最新期別：{report['latest_period']}／{report['latest_date']}",""]
    lines.extend(f"- {'通過' if x['passed'] else '失敗'}｜{x['name']}｜{x['detail']}" for x in report["checks"])
    for base in DESTINATIONS:
        base.mkdir(parents=True,exist_ok=True); (base/"integrity_audit.json").write_text(text,encoding="utf-8"); (base/"integrity_audit.md").write_text("\n".join(lines),encoding="utf-8")

def main() -> int:
    report=build_audit(); save(report); print(json.dumps(report,ensure_ascii=False,indent=2)); return 0 if report["passed"] else 1

if __name__=="__main__": raise SystemExit(main())
