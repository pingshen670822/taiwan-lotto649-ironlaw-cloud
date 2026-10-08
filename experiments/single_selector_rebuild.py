from __future__ import annotations

import json
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))

from engine import (
    MAIN_CUTOFF,
    N,
    apply_production_policy,
    fuse_predictions,
    load_draws,
    logloss,
    model_suite,
    rank_positions,
    rolling_quality,
    single_selector_weights,
)


@dataclass
class Frame:
    actual: np.ndarray
    ensemble: np.ndarray
    uniform_probability: np.ndarray
    performance_probability: np.ndarray
    uniform_top1: np.ndarray
    performance_top1: np.ndarray
    uniform_top3: np.ndarray
    performance_top3: np.ndarray
    uniform_top5: np.ndarray
    performance_top5: np.ndarray
    uniform_rank: np.ndarray
    performance_rank: np.ndarray


def zscore(values: np.ndarray) -> np.ndarray:
    return (values-values.mean())/(values.std()+1e-12)


def vote_signal(preds: np.ndarray, weights: np.ndarray, depth: int) -> np.ndarray:
    signal=np.zeros(N,dtype=float)
    for index,row in enumerate(preds):
        order=np.argsort(row)[::-1][:depth]
        points=np.linspace(1.0,1.0/depth,depth)
        signal[order]+=weights[index]*points
    return signal


def rank_signal(preds: np.ndarray, weights: np.ndarray) -> np.ndarray:
    percentiles=np.empty_like(preds)
    for index,row in enumerate(preds):
        order=np.argsort(row)
        percentiles[index,order]=np.linspace(0.0,1.0,N)
    return np.average(percentiles,axis=0,weights=weights)


def collect_frames(rounds: int=1040) -> tuple[list[Frame],list[str]]:
    draws=load_draws(); start=max(360,len(draws)-rounds)
    names=list(model_suite(draws[:start],False))
    losses={name:[] for name in names}; hits={name:[] for name in names}
    spills={name:[] for name in names}; single_hits={name:[] for name in names}
    weights=np.ones(len(names))/len(names); frames=[]
    for index in range(start,len(draws)):
        models=model_suite(draws[:index],False)
        actual=np.zeros(N); actual[np.array(draws[index].main)-1]=1
        preds=np.stack([models[name] for name in names])
        if losses[names[0]]:
            uniform_ll=logloss(np.full(N,6/N),actual)
            quality=[rolling_quality(hits[name],losses[name],MAIN_CUTOFF*6/49,uniform_ll,spills[name])[0] for name in names]
            q=np.asarray(quality); weights=np.exp(3.0*(q-q.max())); weights/=weights.sum()
            for _ in range(3):
                weights=np.minimum(weights,.25); weights/=weights.sum()
        ensemble=fuse_predictions(preds,weights,6,.25)
        ensemble,_=apply_production_policy(ensemble,draws[index-1].main)
        perf=single_selector_weights(single_hits,names)
        uniform=np.ones(len(names))/len(names)
        frames.append(Frame(
            actual=actual,
            ensemble=ensemble,
            uniform_probability=np.average(preds,axis=0,weights=uniform),
            performance_probability=np.average(preds,axis=0,weights=perf),
            uniform_top1=vote_signal(preds,uniform,1),
            performance_top1=vote_signal(preds,perf,1),
            uniform_top3=vote_signal(preds,uniform,3),
            performance_top3=vote_signal(preds,perf,3),
            uniform_top5=vote_signal(preds,uniform,5),
            performance_top5=vote_signal(preds,perf,5),
            uniform_rank=rank_signal(preds,uniform),
            performance_rank=rank_signal(preds,perf),
        ))
        for model_index,name in enumerate(names):
            ranks=rank_positions(preds[model_index],actual)
            hits[name].append(sum(rank<=MAIN_CUTOFF for rank in ranks))
            spills[name].append(sum(10<=rank<=15 for rank in ranks))
            losses[name].append(logloss(preds[model_index],actual))
            single_hits[name].append(int(actual[int(np.argmax(preds[model_index]))]))
    return frames,names


def candidate_scores(frame: Frame, signal_name: str, alpha: float) -> np.ndarray:
    signal=getattr(frame,signal_name)
    return zscore(frame.ensemble)+alpha*zscore(signal)


def evaluate(frames: list[Frame], signal_name: str, alpha: float, eligible_size: int, cooldown: str, include_picks: bool=False) -> dict:
    picks=[]; values=[]; previous=None; previous_hit=True
    for frame in frames:
        score=candidate_scores(frame,signal_name,alpha)
        eligible=np.argsort(frame.ensemble)[::-1][:eligible_size]
        ordered=eligible[np.argsort(score[eligible])[::-1]]
        pick=int(ordered[0])
        if cooldown=="miss" and previous_hit is False and pick==previous and len(ordered)>1:
            pick=int(ordered[1])
        elif cooldown=="always" and pick==previous and len(ordered)>1:
            pick=int(ordered[1])
        hit=bool(frame.actual[pick]); picks.append(pick+1); values.append(int(hit))
        previous=pick; previous_hit=hit
    development=values[:520]; holdout=values[520:1040]
    result={
        "name":f"{signal_name}|a={alpha:g}|pool={eligible_size}|cooldown={cooldown}",
        "signal":signal_name,
        "alpha":alpha,
        "eligible_size":eligible_size,
        "cooldown":cooldown,
        "development_hits":sum(development),
        "development_first260":sum(development[:260]),
        "development_second260":sum(development[260:]),
        "holdout_hits":sum(holdout),
        "holdout_first260":sum(holdout[:260]),
        "holdout_second260":sum(holdout[260:]),
        "recent20":sum(values[-20:]),
        "recent60":sum(values[-60:]),
        "recent120":sum(values[-120:]),
        "total_hits":sum(values),
        "latest_backtest_pick":picks[-1],
    }
    if include_picks:
        result["picks"]=picks
        result["hits"]=values
    return result


def main() -> None:
    frames,names=collect_frames()
    signals=(
        "uniform_probability","performance_probability",
        "uniform_top1","performance_top1",
        "uniform_top3","performance_top3",
        "uniform_top5","performance_top5",
        "uniform_rank","performance_rank",
    )
    results=[]
    for signal in signals:
        for alpha in (0.0,.1,.25,.5,.75,1.0,1.5,2.0,3.0,4.0):
            for pool in (3,5,9,12,18,49):
                for cooldown in ("none","miss","always"):
                    results.append(evaluate(frames,signal,alpha,pool,cooldown))
    # Model selection is ordered with development data only. Holdout is never part of the sort key.
    results.sort(key=lambda row:(min(row["development_first260"],row["development_second260"]),row["development_hits"]),reverse=True)
    baseline=6/49
    development_pass=[row for row in results if row["development_hits"]/520>baseline and row["development_first260"]/260>baseline and row["development_second260"]/260>baseline]
    selected=development_pass[0] if development_pass else results[0]
    report={
        "method":"pre-registered signal grid; selection and tie-breaking use development only",
        "selection_sort":["minimum development half hits","total development hits","fixed candidate order"],
        "models":names,
        "candidate_count":len(results),
        "baseline":baseline,
        "selected_by_development":selected,
        "selected_passes_holdout":selected["holdout_hits"]/520>baseline,
        "selected_passes_recent":selected["recent60"]/60>=baseline and selected["recent120"]/120>=baseline,
        "top_development_candidates":results[:30],
        "dual_window_pass_count":sum(row["development_hits"]/520>baseline and row["holdout_hits"]/520>baseline for row in results),
    }
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=="__main__":
    main()
