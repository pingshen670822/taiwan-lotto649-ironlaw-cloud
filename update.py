from __future__ import annotations
import csv,json,urllib.parse,urllib.request
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from engine import ROOT,load_draws,analyze
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
    if not any(p["based_on_period"]==result["latest_draw"]["period"] for p in history):
        history.append({"created_at":result["generated_at"],"based_on_period":result["latest_draw"]["period"],"based_on_date":result["latest_draw"]["date"],"target_date":result["target_date"],"status":"pending","packs":result["packs"],"special_packs":result["special_packs"],"avoid":result["avoid"],"suggested_sets":result["suggested_sets"]})
    HISTORY_PATH.write_text(json.dumps(history,ensure_ascii=False,indent=2),encoding="utf-8")
    return history

def main():
    count=update_current_month(); draws=load_draws(); result=analyze(draws)
    result["generated_at"]=datetime.now(ZoneInfo("Asia/Taipei")).isoformat(timespec="seconds")
    if not result["release_gate"]["passed"]: raise RuntimeError("release gate failed; reports and cloud were not overwritten")
    history=settle_and_save(result); build_reports(result,history)
    print(json.dumps({"draws":count,"latest":result["latest_draw"],"target":result["target_date"],"gate":result["release_gate"]},ensure_ascii=False,indent=2))
if __name__=="__main__": main()
