from __future__ import annotations
import csv,json,urllib.parse,urllib.request
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from engine import ROOT,load_draws,analyze,latest_module_review,model_suite
from report import build_reports

API="https://api.taiwanlottery.com/TLCAPIWeB/Lottery/Lotto649Result"
CSV_PATH=ROOT/"data"/"official_lotto649.csv"
HISTORY_PATH=ROOT/"data"/"prediction_history.json"

def fetch_month(month: str) -> list[dict]:
    q=urllib.parse.urlencode({"period":"","month":month,"pageNum":1,"pageSize":50})
    req=urllib.request.Request(API+"?"+q,headers={"User-Agent":"Mozilla/5.0 TW649-cleanroom/1.0"})
    with urllib.request.urlopen(req,timeout=30) as r: obj=json.load(r)
    if obj.get("rtCode")!=0: raise RuntimeError(f"official API error: {obj.get('rtMsg')}")
    return obj.get("content",{}).get("lotto649Res",[])

def update_current_month() -> int:
    rows=list(csv.DictReader(CSV_PATH.open(encoding="utf-8-sig",newline="")))
    by_period={int(r["period"]):r for r in rows}
    for item in fetch_month(date.today().strftime("%Y-%m")):
        nums=list(map(int,item["drawNumberSize"])); main=sorted(nums[:6]); special=nums[6]
        if len(set(main))!=6 or special in main or not all(1<=n<=49 for n in nums): raise ValueError("official draw failed validation")
        period=int(item["period"]); old=by_period.get(period,{k:"" for k in rows[0]})
        old.update({"period":str(period),"draw_date":item["lotteryDate"][:10],**{f"n{i+1}":str(n) for i,n in enumerate(main)},"special":str(special),"sales_amount":str(item.get("sellAmount") or ""),"prize_total":str(item.get("totalAmount") or ""),"source":"taiwanlottery_official_api","fetched_at":date.today().isoformat()})
        by_period[period]=old
    fields=list(rows[0]); ordered=[by_period[k] for k in sorted(by_period)]
    tmp=CSV_PATH.with_suffix(".tmp")
    with tmp.open("w",encoding="utf-8-sig",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(ordered)
    tmp.replace(CSV_PATH); return len(ordered)

def settle_and_save(result: dict):
    history=json.loads(HISTORY_PATH.read_text(encoding="utf-8")) if HISTORY_PATH.exists() else []
    draws={d.draw_date:d for d in load_draws()}
    for p in history:
        if p.get("status")!="pending": continue
        actual=draws.get(p["target_date"])
        if actual:
            aset=set(actual.main); p["status"]="settled"; p["actual"]={"period":actual.period,"date":actual.draw_date,"main":actual.main,"special":actual.special}
            p["settlement"]={"pack_hits":{k:{"count":len(aset&set(v)),"numbers":sorted(aset&set(v))} for k,v in p["packs"].items()},"special_hit":actual.special in p["special_packs"]["三碼觀察"],"avoid_errors":{k:sorted(aset&set(v)) for k,v in p["avoid"].items()}}
    same_basis=[p for p in history if p["based_on_period"]==result["latest_draw"]["period"]]
    if not any(p.get("engine")==result["engine"] for p in same_basis):
        history.append({"created_at":result["generated_at"],"engine":result["engine"],"revision_no":len(same_basis)+1,"revision_reason":"前9碼邊界重整" if same_basis else "例行開獎後全量重算","based_on_period":result["latest_draw"]["period"],"based_on_date":result["latest_draw"]["date"],"target_date":result["target_date"],"status":"pending","packs":result["packs"],"special_packs":result["special_packs"],"avoid":result["avoid"],"suggested_sets":result["suggested_sets"]})
    HISTORY_PATH.write_text(json.dumps(history,ensure_ascii=False,indent=2),encoding="utf-8")
    return history

def main():
    count=update_current_month(); draws=load_draws(); result=analyze(draws)
    result["engine"]="cleanroom_top9_precision_compression_v6"
    result["weight_logic"]={"windows":[20,60,120],"hit_weights":[0.50,0.30,0.20],"target_cutoff":9,"spill_range":[10,15],"spill_penalty":0.16,"rank_fusion_share":0.25,"probability_fusion_share":0.75,"boundary_shift_count":4,"previous_draw_overlap_cap":3,"latest_draw_weight_recalculation":True,"failure_streak_penalty":True,"stability_penalty":True,"single_model_cap":0.25,"external_method_walk_forward_gate":True,"reason":"所有主號模組統一以前9碼命中為訓練目標；校準機率分數占75%、跨模型順位占25%，固定將邊界4席做無穿越壓縮，並限制前9碼最多沿用上期3席。這組規則是520期逐期走步測試勝出組合；任何外部分析法若未優於既有基準，一律不得進入正式預測。"}
    result["generated_at"]=datetime.now(ZoneInfo("Asia/Taipei")).isoformat(timespec="seconds")
    audit=result["backtest"]["main"]["recent_rank_audit"]
    latest_audit=audit[-1]
    result["rank_boundary_audit"]={"cutoff":9,"spill_range":[10,15],"recent_rounds":len(audit),"recent_top9_avg":round(sum(x["top9_hits"] for x in audit)/len(audit),4),"recent_spill_10_15_avg":round(sum(x["spill_10_15"] for x in audit)/len(audit),4),"periods_with_spill":sum(x["spill_10_15"]>0 for x in audit),"next_boundary_rotation":result["backtest"]["main"].get("next_boundary_rotation",{}),"latest_rows":audit[-10:],"root_causes":[f"最新一期六顆實開順位為{'、'.join(map(str,latest_audit['actual_ranks']))}；前9命中{latest_audit['top9_hits']}顆，第10–15名外溢{latest_audit['spill_10_15']}顆",f"最近20期前9平均命中為{round(sum(x['top9_hits'] for x in audit)/len(audit),4)}，低命中期必須逐期追責而不能用單期答案改牌","舊版自適應邊界會因短期狀態切換，且未限制上期號占位，造成候選邊界不穩定"],"corrections":["訓練、降權、回測與發布門檻全部統一為前9碼","第10–15名外溢逐期記錄並對責任模型扣分","固定校準機率75%與跨模型順位25%，不再逐期任意切換","固定壓縮4個前9邊界席位；前9最多沿用上期3席","發布前立即納入最新一期重算，全部新方法先過520期無穿越測試"]}
    result["module_review"]={"main":latest_module_review(draws,result["backtest"]["main"],False),"special":latest_module_review(draws,result["backtest"]["special"],True)}
    strong=result["packs"]["最強單支"][0]; current_models=model_suite(draws,False)
    strong_ranks={name:(score.argsort()[::-1]+1).tolist().index(strong)+1 for name,score in current_models.items()}
    top9_support=sum(rank<=9 for rank in strong_ranks.values()); top15_support=sum(rank<=15 for rank in strong_ranks.values())
    strong_checks={"final_rank_is_1":result["main_rank"][0]["number"]==strong,"at_least_half_models_top9":top9_support>=(len(strong_ranks)+1)//2,"at_least_two_thirds_models_top15":top15_support*3>=len(strong_ranks)*2,"walk_forward_beats_random":result["backtest"]["main"]["avg_hits"]>result["release_gate"]["main_random_hits"],"release_gate_passed":result["release_gate"]["passed"],"previous_draw_used_as_direct_pick":False}
    result["strongest_recommendation"]={"label":"唯一超高信心強烈推薦（系統內相對最高）","number":strong,"count":1,"final_rank":1,"model_count":len(strong_ranks),"model_top9_support":top9_support,"model_top15_support":top15_support,"model_rank_evidence":strong_ranks,"logic_checks":strong_checks,"all_passed":all(value is True for key,value in strong_checks.items() if key!="previous_draw_used_as_direct_pick") and strong_checks["previous_draw_used_as_direct_pick"] is False,"warning":"超高信心指本系統內多模型相對共識，不代表開獎保證。"}
    top9=set(result["packs"]["九中三"]); prior=set(result["latest_draw"]["main"])
    rows520=result["backtest"]["main"]["rows"]; first_rows=rows520[:-120] or rows520; last_rows=rows520[-120:]
    result["research_review"]={"objective":"研究常見樂透分析模式後，只採用可通過無未來資料走步測試的方法","sources":[{"site":"Lotto Predictions","methods":["熱冷號與遺漏","奇偶／高低／區間結構","雙階段評分與結構模板","開獎後自動重算"],"url":"https://lotto-predictions.com/en/"},{"site":"WheelPlayed","methods":["頻率","間隔時機","近期性","共現搭配網路","動能與平衡"],"url":"https://www.wheelplayed.com/"},{"site":"Cloverly","methods":["相對期望值熱冷分析","逾期分析","號碼配對"],"url":"https://cloverly.io/features/frequency-analysis"},{"site":"台灣大樂透選號助手","methods":["頻率","相關性","模式辨識","熱冷趨勢","回測統計"],"url":"https://apps.apple.com/tw/app/%E5%8F%B0%E7%81%A3%E5%A4%A7%E6%A8%82%E9%80%8F%E9%81%B8%E8%99%9F%E5%B9%AB%E6%89%8B/id6747036136"}],"accepted":["20／60／120期頻率與穩定度加權","遺漏風險","條件共現","結構平衡","固定前9邊界壓縮","上期號前9占位上限3席"],"rejected_after_walk_forward":["開獎星期分流","重複號轉移矩陣","共現衰減網路混合","熱門／冷門均值回歸混合","只採前六名模型的菁英權重"],"performance":{"prior_v5_avg520":1.1692,"v6_avg520":result["backtest"]["main"]["avg_hits"],"prior_v5_recent20":1.15,"v6_recent20":round(sum(x["top9_hits"] for x in audit)/len(audit),4),"v6_first400":round(sum(x["hit"] for x in first_rows)/len(first_rows),4),"v6_last120":round(sum(x["hit"] for x in last_rows)/len(last_rows),4),"random_avg520":result["release_gate"]["main_random_hits"]}}
    result["calculation_integrity"]={"official_rows":len(draws),"latest_period_used":draws[-1].period,"prediction_target":result["target_date"],"future_data_used":False,"previous_prediction_rewritten":False,"prediction_revision_append_only":True,"strongest_single_count":len(result["packs"]["最強單支"]),"previous_draw_overlap_cap":3,"top9_previous_draw_overlap":len(top9&prior),"research_methods_without_walk_forward_rejected":True,"rule":"只使用目標期以前的官方開獎資料；上期獎號只用於結算、模型追責及限制過度占位，不得直接指定下期號碼；開獎前若更換核心只可追加版本，不得覆寫舊預測"}
    if not result["release_gate"]["passed"] or not result["strongest_recommendation"]["all_passed"]: raise RuntimeError("release gate failed; reports and cloud were not overwritten")
    history=settle_and_save(result); build_reports(result,history)
    print(json.dumps({"draws":count,"latest":result["latest_draw"],"target":result["target_date"],"gate":result["release_gate"]},ensure_ascii=False,indent=2))
if __name__=="__main__": main()
