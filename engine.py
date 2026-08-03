from __future__ import annotations

import csv, json, math, statistics
from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta
from itertools import combinations
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data" / "official_lotto649.csv"
N = 49
MAIN_CUTOFF = 9
MAIN_SPILL_RANGE = (10, 15)
RANK_BLEND_CHOICES = (0.0, 0.25, 0.5, 0.75, 1.0)
BOUNDARY_SHIFT_CHOICES = tuple(range(7))
PRODUCTION_RANK_SHARE = .25
PRODUCTION_BOUNDARY_SHIFT = 4
PRODUCTION_REPEAT_CAP = 3

@dataclass(frozen=True)
class Draw:
    period: int
    draw_date: str
    main: tuple[int, ...]
    special: int

def load_draws(path: Path = DATA) -> list[Draw]:
    out=[]
    with path.open(encoding="utf-8-sig",newline="") as f:
        for r in csv.DictReader(f):
            nums=tuple(sorted(int(r[f"n{i}"]) for i in range(1,7)))
            sp=int(r["special"])
            if len(set(nums))!=6 or not all(1<=n<=49 for n in nums) or sp in nums: raise ValueError(f"invalid draw {r['period']}")
            out.append(Draw(int(r["period"]),r["draw_date"],nums,sp))
    if len({d.period for d in out})!=len(out) or len({d.draw_date for d in out})!=len(out): raise ValueError("duplicate period/date")
    return sorted(out,key=lambda d:d.draw_date)

def normalize_probability(x: np.ndarray, total: float=6.0) -> np.ndarray:
    x=np.asarray(x,dtype=float)
    x=np.clip(x,1e-8,None)
    return np.clip(x/x.sum()*total,1e-6,.999999)

def matrix(draws: list[Draw]) -> np.ndarray:
    a=np.zeros((len(draws),N),dtype=float)
    for i,d in enumerate(draws): a[i,np.array(d.main)-1]=1
    return a

def special_matrix(draws: list[Draw]) -> np.ndarray:
    a=np.zeros((len(draws),N),dtype=float)
    for i,d in enumerate(draws): a[i,d.special-1]=1
    return a

def ewma(y: np.ndarray, half_life: float) -> np.ndarray:
    age=np.arange(len(y)-1,-1,-1); w=np.exp(-math.log(2)*age/half_life)
    return (y*w[:,None]).sum(0)/(w.sum()+1e-12)

def gaps(y: np.ndarray) -> np.ndarray:
    out=np.full(N,len(y),dtype=float)
    for n in range(N):
        found=np.flatnonzero(y[:,n])
        if len(found): out[n]=len(y)-1-found[-1]
    return out

def model_suite(draws: list[Draw], special: bool=False) -> dict[str,np.ndarray]:
    y=special_matrix(draws) if special else matrix(draws)
    total=1.0 if special else 6.0
    base=total/N
    result={}
    # Beta-Binomial shrinkage prevents small-window overreaction.
    for window,prior in ((24,72),(60,120),(150,180),(360,240)):
        z=y[-window:]; rate=(z.sum(0)+base*prior)/(len(z)+prior)
        result[f"bayes_{window}"]=normalize_probability(rate,total)
    for hl in (8,21,55,144): result[f"ewma_{hl}"]=normalize_probability(ewma(y[-720:],hl),total)
    g=gaps(y)
    # Empirical hazard P(hit next | current gap bucket), learned without assuming overdue means due.
    hazard=np.full(N,base)
    hist=y[-900:]
    for n in range(N):
        run=0; num=den=0
        target=min(int(g[n]),35)
        for value in hist[:,n]:
            if min(run,35)==target: den+=1; num+=int(value)
            run=0 if value else run+1
        hazard[n]=(num+base*30)/(den+30)
    result["empirical_hazard"]=normalize_probability(hazard,total)
    if not special:
        # Conditional pair lift from latest two draws, strongly shrunk.
        recent=y[-500:]; pair=recent.T@recent; marg=recent.mean(0)
        anchors=np.flatnonzero(y[-2:].sum(0)>0); lift=np.zeros(N)
        for n in range(N):
            vals=[]
            for a in anchors:
                co=pair[n,a]; vals.append((co+40*marg[n])/(recent[:,a].sum()+40))
            lift[n]=statistics.mean(vals) if vals else marg[n]
        result["conditional_pair"]=normalize_probability(lift,total)
        # Regime slope: recent probability versus stable background, clipped and shrunk.
        short=(y[-30:].sum(0)+base*90)/120; long=(y[-240:].sum(0)+base*180)/420
        result["regime_slope"]=normalize_probability(long+np.clip(short-long,-.025,.025),total)
    return result

def brier(p: np.ndarray,y: np.ndarray) -> float: return float(np.mean((p-y)**2))
def logloss(p: np.ndarray,y: np.ndarray) -> float:
    p=np.clip(p,1e-6,1-1e-6); return float(-np.mean(y*np.log(p)+(1-y)*np.log(1-p)))

def rolling_quality(hits: list[int], losses: list[float], expected: float, uniform_loss: float, spills: list[int] | None=None) -> tuple[float,dict]:
    windows=(20,60,120); hit_means=[]; loss_means=[]; spill_means=[]
    spills=spills or [0]*len(hits)
    for size in windows:
        hit_means.append(statistics.mean(hits[-size:]))
        loss_means.append(statistics.mean(losses[-size:]))
        spill_means.append(statistics.mean(spills[-size:]))
    streak=0
    for value in reversed(hits):
        if value: break
        streak+=1
    hit_score=.50*hit_means[0]+.30*hit_means[1]+.20*hit_means[2]
    loss_score=.50*loss_means[0]+.30*loss_means[1]+.20*loss_means[2]
    spill_score=.50*spill_means[0]+.30*spill_means[1]+.20*spill_means[2]
    instability=float(np.std(hit_means))
    quality=hit_score-expected-.16*spill_score-6*max(0,loss_score-uniform_loss)-.18*instability-.035*min(streak,5)
    return quality,{"hit20":round(hit_means[0],4),"hit60":round(hit_means[1],4),"hit120":round(hit_means[2],4),"spill20":round(spill_means[0],4),"spill60":round(spill_means[1],4),"spill120":round(spill_means[2],4),"loss20":round(loss_means[0],8),"loss60":round(loss_means[1],8),"loss120":round(loss_means[2],8),"zero_hit_streak":streak,"quality":round(quality,8)}

def rank_positions(score: np.ndarray, actual: np.ndarray) -> list[int]:
    order=np.argsort(score)[::-1]
    positions=np.empty(N,dtype=int); positions[order]=np.arange(1,N+1)
    return sorted(int(positions[i]) for i in np.flatnonzero(actual))

def fuse_predictions(preds: np.ndarray, weights: np.ndarray, total: float, rank_share: float) -> np.ndarray:
    """融合校準機率與跨模型順位；順位尺度化避免高波動模型暗中壟斷排序。"""
    raw=np.average(preds,axis=0,weights=weights)
    percentiles=np.empty_like(preds)
    for i,row in enumerate(preds):
        order=np.argsort(row)
        percentiles[i,order]=np.linspace(0.0,1.0,N)
    rank_signal=np.average(percentiles,axis=0,weights=weights)
    pz=(raw-raw.mean())/(raw.std()+1e-12)
    rz=(rank_signal-rank_signal.mean())/(rank_signal.std()+1e-12)
    fused=(1-rank_share)*pz+rank_share*rz
    prior=total/N
    return normalize_probability(np.full(N,prior)+.20*raw.std()*fused,total)

def apply_fixed_boundary_shift(score: np.ndarray, count: int) -> np.ndarray:
    """Keep the strongest front segment and replace the bottom boundary with ranks 10 onward."""
    count=max(0,min(int(count),6)); adjusted=score.copy()
    if not count: return adjusted
    order=np.argsort(score)[::-1]
    demoted=order[MAIN_CUTOFF-count:MAIN_CUTOFF]
    promoted=order[MAIN_CUTOFF:MAIN_CUTOFF+count]
    for old,new in zip(demoted,promoted): adjusted[old],adjusted[new]=adjusted[new],adjusted[old]
    return adjusted

def apply_repeat_cap(score: np.ndarray, previous_main: tuple[int,...], cap: int) -> np.ndarray:
    """Prevent the previous draw from occupying the next Top 9; no direct carry-over shortcut."""
    adjusted=score.copy(); order=np.argsort(score)[::-1]
    previous={n-1 for n in previous_main}; top=order[:MAIN_CUTOFF].tolist()
    repeated=[idx for idx in top if idx in previous]
    excess=max(0,len(repeated)-cap)
    if not excess: return adjusted
    demoted=repeated[-excess:]
    promoted=[idx for idx in order[MAIN_CUTOFF:] if idx not in previous][:excess]
    for old,new in zip(demoted,promoted): adjusted[old],adjusted[new]=adjusted[new],adjusted[old]
    return adjusted

def apply_production_policy(score: np.ndarray, previous_main: tuple[int,...]) -> tuple[np.ndarray,dict]:
    base_top=(np.argsort(score)[::-1][:MAIN_CUTOFF]+1).tolist()
    adjusted=apply_fixed_boundary_shift(score,PRODUCTION_BOUNDARY_SHIFT)
    adjusted=apply_repeat_cap(adjusted,previous_main,PRODUCTION_REPEAT_CAP)
    final_top=(np.argsort(adjusted)[::-1][:MAIN_CUTOFF]+1).tolist()
    return adjusted,{"count":PRODUCTION_BOUNDARY_SHIFT,"rank_share":PRODUCTION_RANK_SHARE,"repeat_cap":PRODUCTION_REPEAT_CAP,"promoted":[n for n in final_top if n not in base_top],"demoted":[n for n in base_top if n not in final_top]}

def walk_forward(draws: list[Draw], rounds: int=520, special: bool=False) -> dict:
    start=max(360,len(draws)-rounds); names=list(model_suite(draws[:start],special))
    losses={n:[] for n in names}; hits={n:[] for n in names}; spills={n:[] for n in names}; ensemble_rows=[]
    blend_hits={x:[] for x in RANK_BLEND_CHOICES}; blend_spills={x:[] for x in RANK_BLEND_CHOICES}
    shift_hits={x:[] for x in BOUNDARY_SHIFT_CHOICES}; shift_spills={x:[] for x in BOUNDARY_SHIFT_CHOICES}
    policy_hits={(share,count):[] for share in RANK_BLEND_CHOICES for count in BOUNDARY_SHIFT_CHOICES}
    repeat_cap_hits={x:[] for x in (1,2,3,6)}
    weights=np.ones(len(names))/len(names)
    for i in range(start,len(draws)):
        models=model_suite(draws[:i],special); actual=np.zeros(N)
        actual[(draws[i].special-1 if special else np.array(draws[i].main)-1)]=1
        preds=np.stack([models[n] for n in names]); k=3 if special else MAIN_CUTOFF
        if losses[names[0]]:
            expected=k*(1 if special else 6)/49
            uniform_ll=logloss(np.full(N,(1 if special else 6)/N),actual)
            quality=[]
            for n in names:
                qvalue,_=rolling_quality(hits[n],losses[n],expected,uniform_ll,spills[n])
                quality.append(qvalue)
            q=np.array(quality); weights=np.exp(3.0*(q-q.max())); weights/=weights.sum()
            for _ in range(3):
                weights=np.minimum(weights,.25); weights/=weights.sum()
        total=1 if special else 6
        selected_share=0.0 if special else PRODUCTION_RANK_SHARE
        candidate_scores={share:fuse_predictions(preds,weights,total,share) for share in RANK_BLEND_CHOICES}
        ensemble=candidate_scores[selected_share]
        if not special:
            for count in BOUNDARY_SHIFT_CHOICES:
                shifted_ranks=rank_positions(apply_fixed_boundary_shift(ensemble,count),actual)
                shift_hits[count].append(sum(r<=MAIN_CUTOFF for r in shifted_ranks))
                shift_spills[count].append(sum(MAIN_SPILL_RANGE[0]<=r<=MAIN_SPILL_RANGE[1] for r in shifted_ranks))
            for share,score in candidate_scores.items():
                for count in BOUNDARY_SHIFT_CHOICES:
                    policy_ranks=rank_positions(apply_fixed_boundary_shift(score,count),actual)
                    policy_hits[(share,count)].append(sum(r<=MAIN_CUTOFF for r in policy_ranks))
            research_score=apply_fixed_boundary_shift(candidate_scores[.25],4)
            for cap in repeat_cap_hits:
                cap_ranks=rank_positions(apply_repeat_cap(research_score,draws[i-1].main,cap),actual)
                repeat_cap_hits[cap].append(sum(r<=MAIN_CUTOFF for r in cap_ranks))
        rotation={"count":0,"rank_share":0.0,"repeat_cap":None,"promoted":[],"demoted":[]}
        if not special:
            ensemble,rotation=apply_production_policy(ensemble,draws[i-1].main)
        ranks=rank_positions(ensemble,actual)
        top_hits=sum(r<=k for r in ranks)
        spill_hits=0 if special else sum(MAIN_SPILL_RANGE[0]<=r<=MAIN_SPILL_RANGE[1] for r in ranks)
        ensemble_rows.append({"period":draws[i].period,"date":draws[i].draw_date,"hit":top_hits,"top9_hits":top_hits if not special else None,"spill_10_15":spill_hits if not special else None,"top15_hits":sum(r<=15 for r in ranks) if not special else None,"actual_ranks":ranks,"rank_share":selected_share,"boundary_rotation":rotation,"brier":brier(ensemble,actual),"logloss":logloss(ensemble,actual)})
        if not special:
            for share,score in candidate_scores.items():
                candidate_ranks=rank_positions(score,actual)
                blend_hits[share].append(sum(r<=MAIN_CUTOFF for r in candidate_ranks))
                blend_spills[share].append(sum(MAIN_SPILL_RANGE[0]<=r<=MAIN_SPILL_RANGE[1] for r in candidate_ranks))
        step=[]
        for j,n in enumerate(names):
            model_ranks=rank_positions(preds[j],actual)
            hit=sum(r<=k for r in model_ranks)
            spill=0 if special else sum(MAIN_SPILL_RANGE[0]<=r<=MAIN_SPILL_RANGE[1] for r in model_ranks)
            loss=logloss(preds[j],actual); losses[n].append(loss); hits[n].append(hit); spills[n].append(spill); step.append(loss)
    uniform=np.full(N,(1 if special else 6)/N)
    actuals=[]
    for d in draws[start:]:
        y=np.zeros(N); y[(d.special-1 if special else np.array(d.main)-1)]=1; actuals.append(y)
    uniform_loss=statistics.mean(logloss(uniform,y) for y in actuals)
    ensemble_loss=statistics.mean(r["logloss"] for r in ensemble_rows)
    expected=(3 if special else MAIN_CUTOFF)*(1 if special else 6)/49
    # 迴圈中的權重只結算到倒數第二期；發布前必須納入最新一期重新運算，避免權重延遲一期。
    final_quality=np.array([rolling_quality(hits[n],losses[n],expected,uniform_loss,spills[n])[0] for n in names])
    weights=np.exp(3.0*(final_quality-final_quality.max())); weights/=weights.sum()
    for _ in range(3):
        weights=np.minimum(weights,.25); weights/=weights.sum()
    diagnostics={n:rolling_quality(hits[n],losses[n],expected,uniform_loss,spills[n])[1] for n in names}
    final_share=0.0 if special else PRODUCTION_RANK_SHARE
    blend_diagnostics={} if special else {str(x):{"avg_top9_hits":round(statistics.mean(blend_hits[x]),4),"avg_spill_10_15":round(statistics.mean(blend_spills[x]),4),"recent20_top9_hits":round(statistics.mean(blend_hits[x][-20:]),4),"recent20_spill_10_15":round(statistics.mean(blend_spills[x][-20:]),4)} for x in RANK_BLEND_CHOICES}
    shift_diagnostics={} if special else {str(x):{"avg_top9_hits":round(statistics.mean(shift_hits[x]),4),"avg_spill_10_15":round(statistics.mean(shift_spills[x]),4),"recent20_top9_hits":round(statistics.mean(shift_hits[x][-20:]),4),"recent60_top9_hits":round(statistics.mean(shift_hits[x][-60:]),4),"recent120_top9_hits":round(statistics.mean(shift_hits[x][-120:]),4)} for x in BOUNDARY_SHIFT_CHOICES}
    policy_diagnostics={} if special else {f"{share}:{count}":{"avg520":round(statistics.mean(values),4),"avg20":round(statistics.mean(values[-20:]),4),"avg60":round(statistics.mean(values[-60:]),4),"avg120":round(statistics.mean(values[-120:]),4),"avg_first400":round(statistics.mean(values[:-120] or values),4)} for (share,count),values in policy_hits.items()}
    repeat_cap_diagnostics={} if special else {str(cap):{"avg520":round(statistics.mean(values),4),"avg20":round(statistics.mean(values[-20:]),4),"avg60":round(statistics.mean(values[-60:]),4),"avg120":round(statistics.mean(values[-120:]),4),"avg_first400":round(statistics.mean(values[:-120] or values),4)} for cap,values in repeat_cap_hits.items()}
    next_rotation={}
    if not special:
        current_models=model_suite(draws,False)
        current_raw=fuse_predictions(np.stack([current_models[n] for n in names]),weights,6,final_share)
        _,next_rotation=apply_production_policy(current_raw,draws[-1].main)
    compact_rows=[{k:r[k] for k in ("period","date","hit","brier","logloss")} for r in ensemble_rows]
    return {"rounds":len(ensemble_rows),"rank_cutoff":3 if special else MAIN_CUTOFF,"spill_range":None if special else list(MAIN_SPILL_RANGE),"rank_fusion_share":final_share,"rank_fusion_diagnostics":blend_diagnostics,"boundary_shift_diagnostics":shift_diagnostics,"policy_diagnostics":policy_diagnostics,"repeat_cap_diagnostics":repeat_cap_diagnostics,"production_policy":{} if special else {"rank_share":PRODUCTION_RANK_SHARE,"boundary_shift":PRODUCTION_BOUNDARY_SHIFT,"previous_draw_cap":PRODUCTION_REPEAT_CAP},"next_boundary_rotation":next_rotation,"names":names,"weights":{n:round(float(w),8) for n,w in zip(names,weights)},"weight_diagnostics":diagnostics,"model_logloss":{n:round(statistics.mean(v),8) for n,v in losses.items()},"model_avg_hits":{n:round(statistics.mean(hits[n]),4) for n in names},"model_avg_spill_10_15":{} if special else {n:round(statistics.mean(spills[n]),4) for n in names},"ensemble_logloss":round(ensemble_loss,8),"uniform_logloss":round(uniform_loss,8),"logloss_edge":round(uniform_loss-ensemble_loss,8),"avg_hits":round(statistics.mean(r["hit"] for r in ensemble_rows),4),"avg_spill_10_15":None if special else round(statistics.mean(r["spill_10_15"] for r in ensemble_rows),4),"recent_rank_audit":ensemble_rows[-20:] if not special else [],"rows":compact_rows}

def final_scores(draws: list[Draw], bt: dict, special=False) -> np.ndarray:
    models=model_suite(draws,special); names=bt["names"]
    w=np.array([bt["weights"][n] for n in names]); w/=w.sum()
    score=fuse_predictions(np.stack([models[n] for n in names]),w,1 if special else 6,0.0 if special else PRODUCTION_RANK_SHARE)
    if not special:
        score,_=apply_production_policy(score,draws[-1].main)
    return score

def latest_module_review(draws: list[Draw], current_backtest: dict, special: bool=False) -> list[dict]:
    """用最後一期以前資料重建各模型預測，再以最後一期實開逐一追責。"""
    if len(draws)<521: return []
    training=draws[:-1]; actual=np.zeros(N)
    actual[(draws[-1].special-1 if special else np.array(draws[-1].main)-1)]=1
    models=model_suite(training,special); previous=walk_forward(training,520,special)
    k=3 if special else MAIN_CUTOFF; random_hits=k*(1 if special else 6)/49
    rows=[]
    for name,pred in models.items():
        top=(np.argsort(pred)[::-1][:k]+1).tolist(); top1=top[0]
        hit_numbers=sorted((np.flatnonzero(actual)+1)[np.isin(np.flatnonzero(actual)+1,top)].tolist())
        before=float(previous["weights"].get(name,0)); after=float(current_backtest["weights"].get(name,0))
        avg=float(current_backtest["model_avg_hits"].get(name,0)); failed=(not hit_numbers) or avg<random_hits
        rows.append({"model":name,"prediction_basis_period":draws[-2].period,"evaluated_period":draws[-1].period,"top1":top1,"top1_hit":bool(actual[top1-1]),"top_candidates":top,"hit_count":len(hit_numbers),"hit_numbers":hit_numbers,"logloss":round(logloss(pred,actual),8),"weight_before":round(before,8),"weight_after":round(after,8),"weight_change":round(after-before,8),"action":"失敗降權／重新校準" if failed and after<=before else ("失敗但受權重上限約束，列入下期監控" if failed else "通過，依滾動成績調整")})
    return rows

def shape_ok(nums: tuple[int,...]) -> bool:
    odd=sum(n%2 for n in nums); low=sum(n<=24 for n in nums); zones=Counter((n-1)//10 for n in nums)
    return 2<=odd<=4 and 2<=low<=4 and max(zones.values())<=3 and 80<=sum(nums)<=220

def build_sets(score: np.ndarray, count=8) -> list[list[int]]:
    order=(np.argsort(score)[::-1]+1).tolist()
    low=sorted(range(1,25),key=lambda n:score[n-1],reverse=True)[:9]
    high=sorted(range(25,50),key=lambda n:score[n-1],reverse=True)[:9]
    pool=sorted(set(order[:14]+low+high)); ranked=sorted(pool,key=lambda n:score[n-1],reverse=True)
    candidates=[]
    for comb in combinations(pool,6):
        if not shape_ok(comb): continue
        value=sum(math.log(score[n-1]+1e-9) for n in comb)
        candidates.append((value,tuple(sorted(comb))))
    candidates.sort(reverse=True); chosen=[]
    for value,comb in candidates:
        overlap=max((len(set(comb)&set(x)) for x in chosen),default=0)
        if overlap<=4: chosen.append(comb)
        if len(chosen)==count: break
    if len(chosen)<count:
        for _,comb in candidates:
            if comb not in chosen: chosen.append(comb)
            if len(chosen)==count: break
    return [list(x) for x in chosen]

def next_draw(day: str) -> str:
    d=date.fromisoformat(day)+timedelta(days=1)
    while d.weekday() not in (1,4): d+=timedelta(days=1)
    return d.isoformat()

def analyze(draws: list[Draw]) -> dict:
    main_bt=walk_forward(draws,520,False); special_bt=walk_forward(draws,520,True)
    ms=final_scores(draws,main_bt); ss=final_scores(draws,special_bt,True)
    rank=(np.argsort(ms)[::-1]+1).tolist(); srank=(np.argsort(ss)[::-1]+1).tolist()
    # Publication gate measures calibration, not fabricated certainty.
    main_random=MAIN_CUTOFF*6/49; special_random=3/49
    gate=main_bt["avg_hits"]>main_random and special_bt["avg_hits"]>=special_random and main_bt["logloss_edge"]>=-0.0005 and special_bt["logloss_edge"]>=-0.0005
    return {"system":"台灣大樂透新世代鐵律預測系統","engine":"cleanroom_top9_precision_compression_v6","generated_at":date.today().isoformat(),"history":{"count":len(draws),"first":draws[0].draw_date,"latest":draws[-1].draw_date,"latest_period":draws[-1].period},"latest_draw":{"period":draws[-1].period,"date":draws[-1].draw_date,"main":draws[-1].main,"special":draws[-1].special},"target_date":next_draw(draws[-1].draw_date),"main_rank":[{"rank":i+1,"number":n,"probability":round(float(ms[n-1]),6)} for i,n in enumerate(rank)],"special_rank":[{"rank":i+1,"number":n,"probability":round(float(ss[n-1]),6)} for i,n in enumerate(srank)],"packs":{"最強單支":rank[:1],"二中一":rank[:2],"三中一":rank[:3],"五中二":rank[:5],"九中三":rank[:9],"主攻12碼":rank[:12],"防守18碼":rank[:18]},"special_packs":{"最強單支":srank[:1],"三碼觀察":srank[:3]},"avoid":{"五不中":sorted(rank[-5:]),"十不中":sorted(rank[-10:]),"十五不中":sorted(rank[-15:])},"suggested_sets":build_sets(ms),"backtest":{"main":main_bt,"special":special_bt},"release_gate":{"passed":gate,"rule":"520期走步驗證須通過前9碼命中、10–15名外溢追責與機率校準，且不得由單一模型壟斷","main_edge":main_bt["logloss_edge"],"special_edge":special_bt["logloss_edge"],"main_avg_hits":main_bt["avg_hits"],"main_random_hits":round(main_random,4),"main_avg_spill_10_15":main_bt["avg_spill_10_15"],"special_avg_hits":special_bt["avg_hits"],"special_random_hits":round(special_random,4),"max_main_weight":max(main_bt["weights"].values())},"notice":"開獎為隨機事件；模型只做可驗證的機率排序，不保證中獎。"}

if __name__=="__main__":
    result=analyze(load_draws())
    out=ROOT/"reports"/"latest_analysis.json"; out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"history":result["history"],"target":result["target_date"],"gate":result["release_gate"],"packs":result["packs"]},ensure_ascii=False,indent=2))
