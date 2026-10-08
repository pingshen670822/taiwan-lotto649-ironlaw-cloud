from __future__ import annotations
import hashlib,importlib.util,json,shutil,subprocess,sys
from importlib.metadata import version as installed_version
from datetime import datetime,timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from engine import ROOT,load_draws,next_draw
from integrity_audit import build_audit,save as save_integrity_audit

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
    integrity_audit=build_audit(); save_integrity_audit(integrity_audit)
    add("daily_integrity_audit",integrity_audit["passed"],json.dumps(integrity_audit,ensure_ascii=False))
    add("official_history_complete",len(draws)>=2152,f"{len(draws)} draws")
    expected=expected_latest_date()
    add("official_history_latest",draws[-1].draw_date>=expected,f"actual={draws[-1].period} {draws[-1].draw_date}; expected>={expected}")
    today_taipei=datetime.now(ZoneInfo("Asia/Taipei")).date().isoformat()
    add("daily_prediction_recalculated",str(analysis.get("generated_at",""))[:10]==today_taipei,f"generated={analysis.get('generated_at')}; today={today_taipei}")
    add("prediction_target_calendar_correct",analysis.get("target_date")==next_draw(draws[-1].draw_date),f"actual={analysis.get('target_date')}; expected={next_draw(draws[-1].draw_date)}")
    add("release_gate_evaluated",isinstance(analysis["release_gate"].get("passed"),bool),json.dumps(analysis["release_gate"],ensure_ascii=False))
    add("walk_forward_1040_and_520",analysis["backtest"]["main"]["rounds"]==1040 and analysis["backtest"]["special"]["rounds"]==520,f"main={analysis['backtest']['main']['rounds']}; special={analysis['backtest']['special']['rounds']}")
    add("main_backtest_disclosed",all(key in analysis["release_gate"] for key in ("main_avg_hits","main_random_hits","main_edge")),json.dumps(analysis["release_gate"],ensure_ascii=False))
    add("special_backtest_disclosed",all(key in analysis["release_gate"] for key in ("special_avg_hits","special_random_hits","special_edge")),json.dumps(analysis["release_gate"],ensure_ascii=False))
    add("no_model_monopoly",analysis["release_gate"]["max_main_weight"]<=.30,str(analysis["release_gate"]["max_main_weight"]))
    logic=analysis.get("weight_logic",{})
    add("top9_and_single_selector_v9",logic.get("windows")==[20,60,120] and logic.get("target_cutoff")==9 and logic.get("spill_range")==[10,15] and logic.get("rank_fusion_share")==.25 and logic.get("probability_fusion_share")==.75 and logic.get("boundary_shift_count")==4 and logic.get("previous_draw_overlap_cap")==3 and logic.get("single_selector")=="top1_vote_blend_v3_dual_window" and logic.get("single_selector_alpha")==.50 and logic.get("failed_single_repeat_cooldown") is True and logic.get("single_validation_rounds")==1040 and logic.get("development_rounds")==520 and logic.get("holdout_rounds")==520 and logic.get("pattern_audit_required") is True and logic.get("model_gate_must_not_block_official_data_sync") is True and logic.get("external_method_walk_forward_gate") is True and logic.get("latest_draw_weight_recalculation") is True and logic.get("failure_streak_penalty") is True and logic.get("stability_penalty") is True and logic.get("single_model_cap")==.25,json.dumps(logic,ensure_ascii=False))
    main_bt=analysis["backtest"]["main"]; rank_audit=analysis.get("rank_boundary_audit",{})
    add("top9_training_target",main_bt.get("rank_cutoff")==9 and main_bt.get("spill_range")==[10,15],f"cutoff={main_bt.get('rank_cutoff')}; spill={main_bt.get('spill_range')}")
    add("every_draw_rank_boundary_audit",len(main_bt.get("recent_rank_audit",[]))==20 and all(len(x.get("actual_ranks",[]))==6 and "top9_hits" in x and "spill_10_15" in x and "boundary_rotation" in x for x in main_bt.get("recent_rank_audit",[])) and rank_audit.get("cutoff")==9 and isinstance(rank_audit.get("next_boundary_rotation"),dict),f"recent={len(main_bt.get('recent_rank_audit',[]))}; periods_with_spill={rank_audit.get('periods_with_spill')}; next_rotation={rank_audit.get('next_boundary_rotation',{}).get('count')}")
    policy=main_bt.get("production_policy",{})
    add("production_policy_locked",policy=={"rank_share":.25,"boundary_shift":4,"previous_draw_cap":3},json.dumps(policy,ensure_ascii=False))
    research=analysis.get("research_review",{}); perf=research.get("performance",{})
    add("v9_walk_forward_disclosed",perf.get("v9_avg1040")==main_bt.get("avg_hits") and "v9_recent20" in perf and perf.get("random_avg1040")==analysis["release_gate"]["main_random_hits"],json.dumps(perf,ensure_ascii=False))
    add("candidate_49",len(analysis["main_rank"])==49 and len(analysis["special_rank"])==49,"main/special 49")
    add("suggested_sets",len(analysis["suggested_sets"])==8 and all(len(set(x))==6 for x in analysis["suggested_sets"]),"8 valid sets")
    add("strongest_single_exactly_one",len(analysis["packs"]["最強單支"])==1,f"number={analysis['packs']['最強單支']}")
    strong=analysis.get("strongest_recommendation",{})
    selector=main_bt.get("single_selector",{})
    add("strongest_multilogic_evidence",strong.get("count")==1 and strong.get("number")==analysis["packs"]["最強單支"][0] and strong.get("label")=="終極獨隻・全系統最高順位" and strong.get("confidence_status") in ("完整通過","回測審慎級") and strong.get("target_draw_date")==analysis.get("target_date") and strong.get("based_on_period")==analysis.get("latest_draw",{}).get("period") and strong.get("data_cutoff_date")==analysis.get("latest_draw",{}).get("date") and strong.get("calculated_at")==analysis.get("generated_at") and strong.get("module_registry_summary",{}).get("registered")==strong.get("module_registry_summary",{}).get("evaluated") and strong.get("selector")=="top1_vote_blend_v3_dual_window" and selector.get("failed_repeat_cooldown") is True and selector.get("rounds")==1040 and selector.get("dual_window_complete") is True and selector.get("development",{}).get("rounds")==520 and selector.get("holdout",{}).get("rounds")==520 and isinstance(selector.get("passed"),bool) and strong.get("walk_forward_hits")==selector.get("hits_total") and strong.get("walk_forward_hit_rate")==selector.get("hit_rate_total") and strong.get("development")==selector.get("development") and strong.get("holdout")==selector.get("holdout") and strong.get("random_hit_rate")==selector.get("random_rate") and strong.get("model_top9_support",0)*2>=strong.get("model_count",99),json.dumps(strong,ensure_ascii=False))
    module_audit=analysis.get("full_system_module_audit",{}); module_rows=module_audit.get("modules",[])
    add("full_system_module_registry",module_audit.get("registered")==len(module_rows) and module_audit.get("evaluated")==len(module_rows) and len(module_rows)>=24 and module_audit.get("selected_number")==strong.get("number") and all(item.get("weight")==0 for item in module_rows if item.get("status")=="雙區段淘汰"),json.dumps({"registered":module_audit.get("registered"),"evaluated":module_audit.get("evaluated"),"weighted":module_audit.get("weighted"),"zero_weight":module_audit.get("zero_weight"),"selected":module_audit.get("selected_number")},ensure_ascii=False))
    pattern=analysis.get("pattern_audit",{}); methods=pattern.get("methods",{}); baseline=pattern.get("random_rate",6/49)
    accepted=[name for name,item in methods.items() if item.get("development",{}).get("rate",0)>baseline and item.get("holdout",{}).get("rate",0)>baseline]
    add("trajectory_cycle_drag_dual_window_audit",pattern.get("development_rounds")==520 and pattern.get("holdout_rounds")==520 and len(methods)>=10 and pattern.get("accepted_methods")==accepted and all(item.get("accepted")== (name in accepted) for name,item in methods.items()),json.dumps({"accepted":accepted,"methods":len(methods),"conclusion":pattern.get("conclusion")},ensure_ascii=False))
    reviews=analysis.get("module_review",{})
    expected_main=set(analysis["backtest"]["main"]["names"]); expected_special=set(analysis["backtest"]["special"]["names"])
    actual_main={x.get("model") for x in reviews.get("main",[])}; actual_special={x.get("model") for x in reviews.get("special",[])}
    add("every_module_reviewed",actual_main==expected_main and actual_special==expected_special,f"main={len(actual_main)}/{len(expected_main)}, special={len(actual_special)}/{len(expected_special)}")
    integrity=analysis.get("calculation_integrity",{})
    add("no_fake_or_future_data",integrity.get("official_rows")==len(draws) and integrity.get("future_data_used") is False and integrity.get("previous_prediction_rewritten") is False and integrity.get("prediction_revision_append_only") is True and integrity.get("research_methods_without_walk_forward_rejected") is True,json.dumps(integrity,ensure_ascii=False))
    add("previous_draw_overlap_capped",integrity.get("previous_draw_overlap_cap")==3 and integrity.get("top9_previous_draw_overlap",99)<=3,json.dumps(integrity,ensure_ascii=False))
    required=["index.html","latest_battle_report.html","latest_analysis.json","single_pattern_audit.json","prediction_history.json","version.json","self_repair_status.json","integrity_audit.json","integrity_audit.md","style.css","app.js","service-worker.js","manifest.webmanifest"]
    cloud_bases=(ROOT/"reports",ROOT/"site",ROOT/"docs",ROOT/"mobile_cloud",ROOT/"docs/mobile_cloud")
    add("artifacts_complete",all((base/x).exists() for base in cloud_bases for x in required),"desktop, site, Pages and independent mobile files")
    add("report_cloud_sync",all(len({sha(base/x) for base in cloud_bases})==1 for x in required),"all five destinations byte-identical")
    banned=["\u5929\u5929\u6a02","\x74\x69\x61\x6e\x74\x69\x61\x6e\x6c\x65","\x46\x61\x6e\x74\x61\x73\x79","\x43\x61\x6c\x69\x66\x6f\x72\x6e\x69\x61"]
    files=[ROOT/"engine.py",ROOT/"update.py",ROOT/"report.py",ROOT/"README.md",ROOT/"site/index.html",ROOT/"reports/latest_analysis.json"]
    found={term:[str(p.relative_to(ROOT)) for p in files if p.exists() and term.lower() in p.read_text(encoding="utf-8").lower()] for term in banned}
    found={k:v for k,v in found.items() if v}; add("independent_branding",not found,json.dumps(found,ensure_ascii=False))
    workflow=(ROOT/".github/workflows/update.yml").read_text(encoding="utf-8")
    watchdog_workflow=(ROOT/".github/workflows/cloud-watchdog.yml").read_text(encoding="utf-8")
    update_code=(ROOT/"update.py").read_text(encoding="utf-8")
    repair_code=(ROOT/"auto_repair.py").read_text(encoding="utf-8")
    report_code=(ROOT/"report.py").read_text(encoding="utf-8")
    one_click=(ROOT/"一鍵更新並檢測.ps1").read_text(encoding="utf-8")
    ironlaw=json.loads((ROOT/"IRONLAW.json").read_text(encoding="utf-8"))
    python_failures=[]
    for path in sorted([*ROOT.glob("*.py"),*(ROOT/"tests").glob("*.py"),*(ROOT/"experiments").glob("*.py")]):
        try: compile(path.read_text(encoding="utf-8"),str(path),"exec")
        except Exception as exc: python_failures.append(f"{path.relative_to(ROOT)}: {exc}")
    add("python_source_syntax",not python_failures,python_failures or "all Python sources compile")
    dependency_failures=[name for name in ("numpy","requests") if importlib.util.find_spec(name) is None]
    add("runtime_dependencies_available",not dependency_failures,dependency_failures or "numpy, requests")
    pinned_requirements=dict(line.strip().split("==",1) for line in (ROOT/"requirements.txt").read_text(encoding="utf-8").splitlines() if "==" in line)
    version_mismatches=[]
    for name,expected_version in pinned_requirements.items():
        try:
            actual_version=installed_version(name)
            if actual_version!=expected_version: version_mismatches.append(f"{name}: {actual_version} != {expected_version}")
        except Exception as exc: version_mismatches.append(f"{name}: {exc}")
    add("project_dependency_versions_pinned",not version_mismatches,version_mismatches or pinned_requirements)
    json_failures=[]
    json_paths=[ROOT/"IRONLAW.json",ROOT/"data/prediction_history.json",ROOT/"reports/latest_analysis.json",ROOT/"reports/version.json",ROOT/"reports/self_repair_status.json"]
    for path in json_paths:
        try: json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc: json_failures.append(f"{path.relative_to(ROOT)}: {exc}")
    add("critical_json_parseable",not json_failures,json_failures or f"{len(json_paths)} critical JSON files")
    temporary_files=[str(path.relative_to(ROOT)) for path in ROOT.rglob("*") if path.is_file() and path.suffix.lower() in (".tmp",".partial") and ".git" not in path.parts]
    add("no_partial_or_temporary_artifacts",not temporary_files,temporary_files or "none")
    workflow_targets=[ROOT/"auto_repair.py",ROOT/"pages_watchdog.py",ROOT/"cloud_verify.py",ROOT/"update.py",ROOT/"verify.py"]
    add("workflow_targets_exist",all(path.exists() for path in workflow_targets),[str(path.name) for path in workflow_targets if not path.exists()] or "all workflow scripts exist")
    report_html=(ROOT/"docs/mobile_cloud/latest_battle_report.html").read_text(encoding="utf-8")
    report_css=(ROOT/"docs/mobile_cloud/style.css").read_text(encoding="utf-8")
    report_js=(ROOT/"docs/mobile_cloud/app.js").read_text(encoding="utf-8")
    interface_ok=all(marker in report_html for marker in ('台灣大樂透・本期戰報','id="manual-refresh"','id="emergency-repair"','data-tab="decision"','data-tab="models"','data-tab="review"','data-tab="monthly"','data-tab="verify"','data-tab="iron"','終極獨隻・全系統最高順位','終極獨隻 1中1','適用開獎日：','依據第','資料截止','運算時間','回測信心：','全系統模組','全系統模組逐項運算','軌跡、週期與拖牌雙區段稽核','本期其他鐵律號碼','查看獨支強烈驗證與完整運算')) and all(marker in report_css for marker in ('#7f1017','.tab.active','.strong .number','.single-date','.report-details'))
    add("marksix_burgundy_tabbed_interface",interface_ok,"burgundy header, six tabs, strongest recommendation band, expandable evidence, and report sections")
    timestamp_ok=all(marker in report_js for marker in ("taipeiNow","tw649-manual-update-time","tw649-repair-time","showStoredTimes","pageHash","version.hash!==pageHash","目前已是最新，不需重新載入")) and all(marker in report_html for marker in ("cloud-generated-time","last-manual-update","last-repair-time","data-manual-time","data-repair-time"))
    add("visible_update_and_repair_timestamps",timestamp_ok,"calculation, manual update, and repair completion times")
    node=shutil.which("node")
    js_result=subprocess.run([node,"--check",str(ROOT/"docs/mobile_cloud/app.js")],capture_output=True,text=True) if node else None
    add("javascript_syntax",bool(js_result and js_result.returncode==0),"node --check passed" if js_result and js_result.returncode==0 else (js_result.stderr if js_result else "node runtime missing"))
    service_worker=(ROOT/"docs/mobile_cloud/service-worker.js").read_text(encoding="utf-8")
    add("service_worker_upgrade_safe",all(marker in service_worker for marker in ("tw649-dual-window-v12","skipWaiting","clients.claim","cache:'no-store'")),"cache v12, immediate activation, old-cache cleanup, network-first")
    auto_rules=["5 16 * * *" in workflow,"25-55/5 13 * * 2,5" in workflow,"0-30/5 14 * * 2,5" in workflow,"40-50/10 14 * * 2,5" in workflow,"0-50/10 15 * * 2,5" in workflow,"0-30/10 16 * * 2,5" in workflow,"python auto_repair.py" in workflow,"pip install -r requirements.txt" in workflow,"TODAY_TAIPEI" in workflow,"PUBLISHED_DATE" in workflow,"大樂透每日預測日期與資料重算" in workflow,"TODAY_TAIPEI" in watchdog_workflow,"PUBLISHED_DATE" in watchdog_workflow,"requests.get" in update_code,"auto_repair.py" in one_click,"push:" in watchdog_workflow,"paths: [\"docs/**\"]" in watchdog_workflow,"sleep 75" in watchdog_workflow,"python pages_watchdog.py" in watchdog_workflow,"35-55/10 14 * * 2,5" in watchdog_workflow,"5-35/10 16 * * 2,5" in watchdog_workflow,"MAX_ATTEMPTS=3" in (ROOT/"pages_watchdog.py").read_text(encoding="utf-8"),"update.py" in repair_code,"verify.py" in repair_code,"snapshot_state" in repair_code,"restore_state" in repair_code,"MAX_ATTEMPTS = 3" in repair_code,"RETRY_SECONDS = 60" in repair_code,"git diff --quiet -- data/official_lotto649.csv" in workflow,"git add data reports site docs mobile_cloud" in workflow,"update_current_month()" in update_code,"settle_and_save(result)" in update_code,"latest_module_review" in update_code,ironlaw.get("automatic_update_locked") is True,ironlaw.get("failed_validation_must_not_publish") is True,ironlaw.get("every_module_must_be_reviewed") is True,ironlaw.get("main_training_cutoff_locked")==9,ironlaw.get("rank_spill_audit_locked")==[10,15],ironlaw.get("rank_spill_penalty_required") is True,ironlaw.get("rank_fusion_share_locked")==.25,ironlaw.get("boundary_shift_count_locked")==4,ironlaw.get("previous_draw_overlap_cap_locked")==3,ironlaw.get("external_method_walk_forward_gate_required") is True,ironlaw.get("rejected_method_must_not_publish") is True,ironlaw.get("latest_draw_weight_recalculation_required") is True,ironlaw.get("every_draw_rank_boundary_audit_required") is True,ironlaw.get("strongest_multilogic_evidence_required") is True,ironlaw.get("autonomous_repair_required") is True,ironlaw.get("after_draw_repair_deadline_minutes")==120,ironlaw.get("repair_retry_interval_minutes")==10,ironlaw.get("mobile_foreground_refresh_required") is True,ironlaw.get("mobile_version_poll_seconds")==60,ironlaw.get("mobile_manual_update_button_required") is True,ironlaw.get("mobile_immediate_repair_button_required") is True,ironlaw.get("manual_repair_must_not_false_report_success") is True,ironlaw.get("daily_prediction_refresh_required") is True,ironlaw.get("daily_prediction_refresh_taipei_after")=="00:05",ironlaw.get("no_new_draw_daily_refresh_must_publish") is True,ironlaw.get("same_day_duplicate_prediction_commit_forbidden") is True,ironlaw.get("prediction_target_date_must_match_next_draw_calendar") is True,ironlaw.get("previous_month_recovery_fetch_required") is True,ironlaw.get("atomic_last_valid_rollback_required") is True,ironlaw.get("daily_integrity_audit_required") is True,ironlaw.get("live_cloud_sync_verification_required") is True,ironlaw.get("pages_deployment_watchdog_required") is True,ironlaw.get("pages_push_immediate_watchdog_required") is True,ironlaw.get("pages_repair_max_attempts")==3,"visibilitychange" in report_code,"setInterval(refreshVersion,60000)" in report_code,"cache:'no-store'" in report_code,"id=\"manual-refresh\"" in report_code,"id=\"emergency-repair\"" in report_code,"manualUpdateLatest" in report_code,"immediateRepair" in report_code,"attempt<=3" in report_code,"validateCloudBundle" in report_code]
    auto_rules.extend([ironlaw.get("battle_report_interface_spec")=="marksix_burgundy_tabbed_v7",ironlaw.get("marksix_interface_tabs_required") is True,ironlaw.get("strongest_single_independent_walk_forward_required") is True,ironlaw.get("strongest_single_always_named_ultimate_highest_rank") is True,ironlaw.get("strongest_single_confidence_must_be_separately_disclosed") is True,ironlaw.get("strongest_single_date_metadata_required") is True,ironlaw.get("all_registered_modules_daily_evaluation_required") is True,ironlaw.get("unverified_pattern_module_zero_weight_required") is True,ironlaw.get("strongest_single_module_registry_evidence_required") is True,ironlaw.get("strongest_single_validation_rounds")==1040,ironlaw.get("strongest_single_development_rounds")==520,ironlaw.get("strongest_single_holdout_rounds")==520,ironlaw.get("strongest_single_dual_window_gate_required") is True,ironlaw.get("trajectory_cycle_drag_pattern_audit_required") is True,ironlaw.get("single_segment_cherry_picking_forbidden") is True,ironlaw.get("unverified_ninety_percent_claim_forbidden") is True,ironlaw.get("model_gate_must_not_block_official_data_sync") is True,ironlaw.get("live_cloud_verification_must_mark_healthy") is True,ironlaw.get("manual_update_taipei_timestamp_required") is True,ironlaw.get("repair_completion_taipei_timestamp_required") is True,ironlaw.get("full_fault_scan_required_before_publish") is True,"python auto_repair.py" in watchdog_workflow,"self_repair_status.json" in watchdog_workflow,"write_healthy_status" in (ROOT/"pages_watchdog.py").read_text(encoding="utf-8"),"build_pattern_audit" in update_code,"full_system_module_audit" in update_code,"終極獨隻・全系統最高順位" in update_code,"confidence_status" in update_code,"taipeiNow" in report_code,"pageHash" in report_code,"version.hash!==pageHash" in report_code,"目前已是最新，不需重新載入" in report_code,"tw649-manual-update-time" in report_code,"tw649-repair-time" in report_code,"tw649-dual-window-v12" in report_code])
    add("automatic_update_ironlaw",all(auto_rules),f"{sum(auto_rules)}/{len(auto_rules)} locked rules present")
    report={"system":analysis["system"],"generated_at":analysis["generated_at"],"passed":all(x["passed"] for x in checks),"latest_period":draws[-1].period,"latest_date":draws[-1].draw_date,"target_date":analysis["target_date"],"checks":checks}
    text=json.dumps(report,ensure_ascii=False,indent=2)
    for base in cloud_bases: (base/"self_test_report.json").write_text(text,encoding="utf-8")
    if hasattr(sys.stdout,"buffer"):
        sys.stdout.buffer.write(text.encode("utf-8")+b"\n")
    else:
        print(text)
    return 0 if report["passed"] else 1
if __name__=="__main__": sys.exit(main())
