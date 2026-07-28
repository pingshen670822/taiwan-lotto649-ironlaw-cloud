from __future__ import annotations
import csv,json,urllib.parse,urllib.request
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from engine import ROOT,load_draws,analyze,latest_module_review
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
    result["engine"]="cleanroom_top9_boundary_rotation_v5"
    result["weight_logic"]={"windows":[20,60,120],"hit_weights":[0.50,0.30,0.20],"target_cutoff":9,"spill_range":[10,15],"spill_penalty":0.16,"adaptive_rank_fusion":True,"adaptive_boundary_rotation":True,"boundary_rotation_cooldown_draws":2,"candidate_failure_tenure":True,"latest_draw_weight_recalculation":True,"failure_streak_penalty":True,"stability_penalty":True,"single_model_cap":0.25,"reason":"所有主號模組統一改以前9碼命中為訓練目標；實開落在第10–15名視為邊界外溢失敗並扣分。連續失效時撤換長期佔位未中的第4–9名候選，將第10–15名高共識候選升入前9，且至少維持兩期觀察，避免一次命中就過早撤銷修正。發布前再納入最新一期立即重算權重。"}
    result["generated_at"]=datetime.now(ZoneInfo("Asia/Taipei")).isoformat(timespec="seconds")
    audit=result["backtest"]["main"]["recent_rank_audit"]
    result["rank_boundary_audit"]={"cutoff":9,"spill_range":[10,15],"recent_rounds":len(audit),"recent_top9_avg":round(sum(x["top9_hits"] for x in audit)/len(audit),4),"recent_spill_10_15_avg":round(sum(x["spill_10_15"] for x in audit)/len(audit),4),"periods_with_spill":sum(x["spill_10_15"]>0 for x in audit),"next_boundary_rotation":result["backtest"]["main"].get("next_boundary_rotation",{}),"latest_rows":audit[-10:],"root_causes":["舊版以12碼命中作為權重獎勵，導致第10–12名被誤判為成功","模型權重在走步迴圈結束後未再納入最新一期，造成發布權重延遲一期","不同模型分數振幅不一，直接平均會讓高振幅模型影響候選邊界","一次邊界修正後若立即恢復舊排序，會再次讓命中退回第10–15名"],"corrections":["訓練、降權、回測與發布門檻全部統一為前9碼","第10–15名外溢逐期記錄並對責任模型扣分","發布前立即重算最新權重","採無未來資料的自適應機率／順位融合，選擇比例只看當期以前成績","連續失效啟動前9邊界升降，修正至少維持兩期並追蹤候選佔位失敗期數"]}
    result["module_review"]={"main":latest_module_review(draws,result["backtest"]["main"],False),"special":latest_module_review(draws,result["backtest"]["special"],True)}
    result["calculation_integrity"]={"official_rows":len(draws),"latest_period_used":draws[-1].period,"prediction_target":result["target_date"],"future_data_used":False,"previous_prediction_rewritten":False,"prediction_revision_append_only":True,"strongest_single_count":len(result["packs"]["最強單支"]),"rule":"只使用目標期以前的官方開獎資料；上期獎號只用於結算與模型追責，不得直接指定下期號碼；開獎前若更換核心只可追加版本，不得覆寫舊預測"}
    if not result["release_gate"]["passed"]: raise RuntimeError("release gate failed; reports and cloud were not overwritten")
    history=settle_and_save(result); build_reports(result,history)
    print(json.dumps({"draws":count,"latest":result["latest_draw"],"target":result["target_date"],"gate":result["release_gate"]},ensure_ascii=False,indent=2))
if __name__=="__main__": main()
