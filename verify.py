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
    logic=analysis.get("weight_logic",{})
    add("top9_precision_compression_v6",logic.get("windows")==[20,60,120] and logic.get("target_cutoff")==9 and logic.get("spill_range")==[10,15] and logic.get("rank_fusion_share")==.25 and logic.get("probability_fusion_share")==.75 and logic.get("boundary_shift_count")==4 and logic.get("previous_draw_overlap_cap")==3 and logic.get("external_method_walk_forward_gate") is True and logic.get("latest_draw_weight_recalculation") is True and logic.get("failure_streak_penalty") is True and logic.get("stability_penalty") is True and logic.get("single_model_cap")==.25,json.dumps(logic,ensure_ascii=False))
    main_bt=analysis["backtest"]["main"]; rank_audit=analysis.get("rank_boundary_audit",{})
    add("top9_training_target",main_bt.get("rank_cutoff")==9 and main_bt.get("spill_range")==[10,15],f"cutoff={main_bt.get('rank_cutoff')}; spill={main_bt.get('spill_range')}")
    add("every_draw_rank_boundary_audit",len(main_bt.get("recent_rank_audit",[]))==20 and all(len(x.get("actual_ranks",[]))==6 and "top9_hits" in x and "spill_10_15" in x and "boundary_rotation" in x for x in main_bt.get("recent_rank_audit",[])) and rank_audit.get("cutoff")==9 and isinstance(rank_audit.get("next_boundary_rotation"),dict),f"recent={len(main_bt.get('recent_rank_audit',[]))}; periods_with_spill={rank_audit.get('periods_with_spill')}; next_rotation={rank_audit.get('next_boundary_rotation',{}).get('count')}")
    policy=main_bt.get("production_policy",{})
    add("production_policy_locked",policy=={"rank_share":.25,"boundary_shift":4,"previous_draw_cap":3},json.dumps(policy,ensure_ascii=False))
    research=analysis.get("research_review",{}); perf=research.get("performance",{})
    add("v6_walk_forward_improves_v5",perf.get("v6_avg520")==main_bt.get("avg_hits") and perf.get("v6_avg520",0)>perf.get("prior_v5_avg520",99) and perf.get("v6_recent20",0)>perf.get("prior_v5_recent20",99),json.dumps(perf,ensure_ascii=False))
    add("candidate_49",len(analysis["main_rank"])==49 and len(analysis["special_rank"])==49,"main/special 49")
    add("suggested_sets",len(analysis["suggested_sets"])==8 and all(len(set(x))==6 for x in analysis["suggested_sets"]),"8 valid sets")
    add("strongest_single_exactly_one",len(analysis["packs"]["最強單支"])==1,f"number={analysis['packs']['最強單支']}")
    strong=analysis.get("strongest_recommendation",{})
    add("strongest_multilogic_evidence",strong.get("count")==1 and strong.get("number")==analysis["packs"]["最強單支"][0] and strong.get("final_rank")==1 and strong.get("model_top9_support",0)*2>=strong.get("model_count",99) and strong.get("model_top15_support",0)*3>=strong.get("model_count",99)*2 and strong.get("all_passed") is True,json.dumps(strong,ensure_ascii=False))
    reviews=analysis.get("module_review",{})
    expected_main=set(analysis["backtest"]["main"]["names"]); expected_special=set(analysis["backtest"]["special"]["names"])
    actual_main={x.get("model") for x in reviews.get("main",[])}; actual_special={x.get("model") for x in reviews.get("special",[])}
    add("every_module_reviewed",actual_main==expected_main and actual_special==expected_special,f"main={len(actual_main)}/{len(expected_main)}, special={len(actual_special)}/{len(expected_special)}")
    integrity=analysis.get("calculation_integrity",{})
    add("no_fake_or_future_data",integrity.get("official_rows")==len(draws) and integrity.get("future_data_used") is False and integrity.get("previous_prediction_rewritten") is False and integrity.get("prediction_revision_append_only") is True and integrity.get("research_methods_without_walk_forward_rejected") is True,json.dumps(integrity,ensure_ascii=False))
    add("previous_draw_overlap_capped",integrity.get("previous_draw_overlap_cap")==3 and integrity.get("top9_previous_draw_overlap",99)<=3,json.dumps(integrity,ensure_ascii=False))
    required=["index.html","latest_battle_report.html","latest_analysis.json","prediction_history.json","version.json","self_repair_status.json","style.css","app.js","service-worker.js","manifest.webmanifest"]
    cloud_bases=(ROOT/"reports",ROOT/"site",ROOT/"docs",ROOT/"mobile_cloud",ROOT/"docs/mobile_cloud")
    add("artifacts_complete",all((base/x).exists() for base in cloud_bases for x in required),"desktop, site, Pages and independent mobile files")
    add("report_cloud_sync",all(len({sha(base/x) for base in cloud_bases})==1 for x in required),"all five destinations byte-identical")
    banned=["\u5929\u5929\u6a02","\x74\x69\x61\x6e\x74\x69\x61\x6e\x6c\x65","\x46\x61\x6e\x74\x61\x73\x79","\x43\x61\x6c\x69\x66\x6f\x72\x6e\x69\x61"]
    files=[ROOT/"engine.py",ROOT/"update.py",ROOT/"report.py",ROOT/"README.md",ROOT/"site/index.html",ROOT/"reports/latest_analysis.json"]
    found={term:[str(p.relative_to(ROOT)) for p in files if p.exists() and term.lower() in p.read_text(encoding="utf-8").lower()] for term in banned}
    found={k:v for k,v in found.items() if v}; add("independent_branding",not found,json.dumps(found,ensure_ascii=False))
    workflow=(ROOT/".github/workflows/update.yml").read_text(encoding="utf-8")
    update_code=(ROOT/"update.py").read_text(encoding="utf-8")
    repair_code=(ROOT/"auto_repair.py").read_text(encoding="utf-8")
    report_code=(ROOT/"report.py").read_text(encoding="utf-8")
    ironlaw=json.loads((ROOT/"IRONLAW.json").read_text(encoding="utf-8"))
    auto_rules=["25-55/5 13 * * 2,5" in workflow,"0-30/5 14 * * 2,5" in workflow,"40-50/10 14 * * 2,5" in workflow,"0-50/10 15 * * 2,5" in workflow,"0-30/10 16 * * 2,5" in workflow,"python auto_repair.py" in workflow,"update.py" in repair_code,"verify.py" in repair_code,"MAX_ATTEMPTS = 3" in repair_code,"RETRY_SECONDS = 60" in repair_code,"git add data reports site docs mobile_cloud" in workflow,"update_current_month()" in update_code,"settle_and_save(result)" in update_code,"latest_module_review" in update_code,ironlaw.get("automatic_update_locked") is True,ironlaw.get("failed_validation_must_not_publish") is True,ironlaw.get("every_module_must_be_reviewed") is True,ironlaw.get("main_training_cutoff_locked")==9,ironlaw.get("rank_spill_audit_locked")==[10,15],ironlaw.get("rank_spill_penalty_required") is True,ironlaw.get("rank_fusion_share_locked")==.25,ironlaw.get("boundary_shift_count_locked")==4,ironlaw.get("previous_draw_overlap_cap_locked")==3,ironlaw.get("external_method_walk_forward_gate_required") is True,ironlaw.get("rejected_method_must_not_publish") is True,ironlaw.get("latest_draw_weight_recalculation_required") is True,ironlaw.get("every_draw_rank_boundary_audit_required") is True,ironlaw.get("strongest_multilogic_evidence_required") is True,ironlaw.get("autonomous_repair_required") is True,ironlaw.get("after_draw_repair_deadline_minutes")==120,ironlaw.get("repair_retry_interval_minutes")==10,ironlaw.get("mobile_foreground_refresh_required") is True,ironlaw.get("mobile_version_poll_seconds")==60,"visibilitychange" in report_code,"setInterval(refreshVersion,60000)" in report_code,"cache:'no-store'" in report_code]
    add("automatic_update_ironlaw",all(auto_rules),f"{sum(auto_rules)}/{len(auto_rules)} locked rules present")
    report={"system":analysis["system"],"generated_at":analysis["generated_at"],"passed":all(x["passed"] for x in checks),"latest_period":draws[-1].period,"latest_date":draws[-1].draw_date,"target_date":analysis["target_date"],"checks":checks}
    text=json.dumps(report,ensure_ascii=False,indent=2)
    for base in cloud_bases: (base/"self_test_report.json").write_text(text,encoding="utf-8")
    print(text); return 0 if report["passed"] else 1
if __name__=="__main__": sys.exit(main())
