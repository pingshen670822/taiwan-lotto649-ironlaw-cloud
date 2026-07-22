from __future__ import annotations
import hashlib,json,sys
from datetime import datetime,timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from engine import ROOT,load_draws

def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def expected_latest_date():
    """依台灣時間判斷應有的最近開獎日；開獎日晚間22:30前不誤判尚未公告。"""
    now=datetime.now(ZoneInfo("Asia/Taipei"))
    day=now.date()
    if day.weekday() in (1,4) and now.hour<22 or (day.weekday() in (1,4) and now.hour==22 and now.minute<30):
        day-=timedelta(days=1)
    while day.weekday() not in (1,4): day-=timedelta(days=1)
    return day.isoformat()
def main():
    checks=[]
    def add(name,ok,detail): checks.append({"name":name,"passed":bool(ok),"detail":detail})
    draws=load_draws(); analysis=json.loads((ROOT/"reports/latest_analysis.json").read_text(encoding="utf-8"))
    add("official_history_complete",len(draws)>=2152,f"{len(draws)} draws")
    expected=expected_latest_date()
    add("official_history_latest",draws[-1].draw_date>=expected,f"actual={draws[-1].period} {draws[-1].draw_date}; expected>={expected}")
    add("release_gate",analysis["release_gate"]["passed"],json.dumps(analysis["release_gate"],ensure_ascii=False))
    add("walk_forward_520",analysis["backtest"]["main"]["rounds"]==520,str(analysis["backtest"]["main"]["rounds"]))
    add("main_hit_edge",analysis["release_gate"]["main_avg_hits"]>analysis["release_gate"]["main_random_hits"],f"{analysis['release_gate']['main_avg_hits']} > {analysis['release_gate']['main_random_hits']}")
    add("special_hit_edge",analysis["release_gate"]["special_avg_hits"]>analysis["release_gate"]["special_random_hits"],f"{analysis['release_gate']['special_avg_hits']} > {analysis['release_gate']['special_random_hits']}")
    add("no_model_monopoly",analysis["release_gate"]["max_main_weight"]<=.30,str(analysis["release_gate"]["max_main_weight"]))
    add("candidate_49",len(analysis["main_rank"])==49 and len(analysis["special_rank"])==49,"main/special 49")
    add("suggested_sets",len(analysis["suggested_sets"])==8 and all(len(set(x))==6 for x in analysis["suggested_sets"]),"8 valid sets")
    add("strongest_single_exactly_one",len(analysis["packs"]["最強單支"])==1,f"number={analysis['packs']['最強單支']}")
    reviews=analysis.get("module_review",{})
    expected_main=set(analysis["backtest"]["main"]["names"]); expected_special=set(analysis["backtest"]["special"]["names"])
    actual_main={x.get("model") for x in reviews.get("main",[])}; actual_special={x.get("model") for x in reviews.get("special",[])}
    add("every_module_reviewed",actual_main==expected_main and actual_special==expected_special,f"main={len(actual_main)}/{len(expected_main)}, special={len(actual_special)}/{len(expected_special)}")
    integrity=analysis.get("calculation_integrity",{})
    add("no_fake_or_future_data",integrity.get("official_rows")==len(draws) and integrity.get("future_data_used") is False and integrity.get("previous_prediction_rewritten") is False,json.dumps(integrity,ensure_ascii=False))
    required=["index.html","latest_battle_report.html","latest_analysis.json","prediction_history.json","version.json","style.css","app.js","service-worker.js","manifest.webmanifest"]
    cloud_bases=(ROOT/"reports",ROOT/"site",ROOT/"docs",ROOT/"mobile_cloud",ROOT/"docs/mobile_cloud")
    add("artifacts_complete",all((base/x).exists() for base in cloud_bases for x in required),"desktop, site, Pages and independent mobile files")
    add("report_cloud_sync",all(len({sha(base/x) for base in cloud_bases})==1 for x in required),"all five destinations byte-identical")
    banned=["天天樂","tiantianle","Fantasy","California"]
    files=[ROOT/"engine.py",ROOT/"update.py",ROOT/"report.py",ROOT/"README.md",ROOT/"site/index.html",ROOT/"reports/latest_analysis.json"]
    found={term:[str(p.relative_to(ROOT)) for p in files if p.exists() and term.lower() in p.read_text(encoding="utf-8").lower()] for term in banned}
    found={k:v for k,v in found.items() if v}; add("independent_branding",not found,json.dumps(found,ensure_ascii=False))
    workflow=(ROOT/".github/workflows/update.yml").read_text(encoding="utf-8")
    update_code=(ROOT/"update.py").read_text(encoding="utf-8")
    ironlaw=json.loads((ROOT/"IRONLAW.json").read_text(encoding="utf-8"))
    auto_rules=["25-55/5 13 * * 2,5" in workflow,"0-30/5 14 * * 2,5" in workflow,"python update.py" in workflow,"python verify.py" in workflow,"git add data reports site docs mobile_cloud" in workflow,"update_current_month()" in update_code,"settle_and_save(result)" in update_code,"latest_module_review" in update_code,ironlaw.get("automatic_update_locked") is True,ironlaw.get("failed_validation_must_not_publish") is True,ironlaw.get("every_module_must_be_reviewed") is True]
    add("automatic_update_ironlaw",all(auto_rules),f"{sum(auto_rules)}/{len(auto_rules)} locked rules present")
    report={"system":analysis["system"],"generated_at":analysis["generated_at"],"passed":all(x["passed"] for x in checks),"latest_period":draws[-1].period,"latest_date":draws[-1].draw_date,"target_date":analysis["target_date"],"checks":checks}
    text=json.dumps(report,ensure_ascii=False,indent=2)
    for base in cloud_bases: (base/"self_test_report.json").write_text(text,encoding="utf-8")
    print(text); return 0 if report["passed"] else 1
if __name__=="__main__": sys.exit(main())
