from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine import N, load_draws, matrix, normalize_probability


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports" / "single_pattern_audit.json"
MIN_TRAIN = 600
HOLDOUT = 520


def top_pick(score: np.ndarray) -> int:
    return int(np.argmax(score) + 1)


def frequency(y: np.ndarray, window: int) -> np.ndarray:
    prior = 6 / N
    z = y[-window:]
    return normalize_probability((z.sum(0) + prior * 120) / (len(z) + 120), 6)


def weekday_cycle(y: np.ndarray, weekdays: np.ndarray, target_weekday: int) -> np.ndarray:
    mask = weekdays == target_weekday
    z = y[mask][-520:]
    prior = y[-1040:].mean(0)
    return normalize_probability((z.sum(0) + prior * 180) / (len(z) + 180), 6)


def transition_drag(y: np.ndarray, lookback: int = 720) -> np.ndarray:
    z = y[-(lookback + 1):]
    source = z[:-1]
    target = z[1:]
    transitions = source.T @ target
    anchors = np.flatnonzero(z[-1])
    marginal = target.mean(0)
    values = []
    for anchor in anchors:
        den = source[:, anchor].sum()
        values.append((transitions[anchor] + marginal * 90) / (den + 90))
    return normalize_probability(np.mean(values, axis=0), 6)


def two_step_drag(y: np.ndarray, lookback: int = 720) -> np.ndarray:
    z = y[-(lookback + 2):]
    source = z[:-2]
    target = z[2:]
    transitions = source.T @ target
    anchors = np.flatnonzero(z[-2])
    marginal = target.mean(0)
    values = []
    for anchor in anchors:
        den = source[:, anchor].sum()
        values.append((transitions[anchor] + marginal * 90) / (den + 90))
    return normalize_probability(np.mean(values, axis=0), 6)


def neighbor_track(y: np.ndarray, lookback: int = 720) -> np.ndarray:
    z = y[-(lookback + 1):]
    delta = Counter()
    for previous, current in zip(z[:-1], z[1:]):
        left = np.flatnonzero(previous) + 1
        right = np.flatnonzero(current) + 1
        for a in left:
            for b in right:
                delta[int(b - a)] += 1
    anchors = np.flatnonzero(z[-1]) + 1
    score = np.zeros(N)
    total = sum(delta.values()) + 1
    for number in range(1, N + 1):
        score[number - 1] = sum((delta[number - int(a)] + 12) / (total + 12 * 97) for a in anchors)
    return normalize_probability(score, 6)


def interval_recurrence(y: np.ndarray, max_gap: int = 36) -> np.ndarray:
    score = np.zeros(N)
    base = 6 / N
    for number in range(N):
        hits = np.flatnonzero(y[:, number])
        if len(hits) < 10:
            score[number] = base
            continue
        intervals = np.diff(hits)
        current_gap = len(y) - int(hits[-1])
        bucket = min(current_gap, max_gap)
        numerator = np.count_nonzero(np.minimum(intervals, max_gap) == bucket)
        denominator = len(intervals)
        score[number] = (numerator + base * 45) / (denominator + 45)
    return normalize_probability(score, 6)


def cooccurrence_drag(y: np.ndarray, lookback: int = 720) -> np.ndarray:
    z = y[-lookback:]
    pairs = z.T @ z
    anchors = np.flatnonzero(y[-2:].sum(0) > 0)
    marginal = z.mean(0)
    values = []
    for anchor in anchors:
        den = z[:, anchor].sum()
        values.append((pairs[anchor] + marginal * 90) / (den + 90))
    return normalize_probability(np.mean(values, axis=0), 6)


def zscore(x: np.ndarray) -> np.ndarray:
    return (x - x.mean()) / (x.std() + 1e-12)


def rolling_rate(values: list[int], window: int) -> float:
    baseline = 6 / N
    z = values[-window:]
    return (sum(z) + baseline * 36) / (len(z) + 36)


def build_audit(draws=None) -> dict:
    draws = draws or load_draws()
    y = matrix(draws)
    weekdays = np.array([__import__("datetime").date.fromisoformat(d.draw_date).weekday() for d in draws])
    base_names = [
        "frequency_60",
        "frequency_240",
        "weekday_cycle",
        "transition_drag",
        "two_step_drag",
        "neighbor_track",
        "interval_recurrence",
        "cooccurrence_drag",
        "multi_condition_equal",
    ]
    meta_names = ["rolling_best_60", "rolling_best_120", "performance_vote_60", "performance_vote_120"]
    names = base_names + meta_names
    hits = {name: [] for name in names}
    picks = {name: [] for name in names}
    start = max(MIN_TRAIN, len(draws) - 1040)
    for i in range(start, len(draws)):
        train = y[:i]
        components = {
            "frequency_60": frequency(train, 60),
            "frequency_240": frequency(train, 240),
            "weekday_cycle": weekday_cycle(train, weekdays[:i], weekdays[i]),
            "transition_drag": transition_drag(train),
            "two_step_drag": two_step_drag(train),
            "neighbor_track": neighbor_track(train),
            "interval_recurrence": interval_recurrence(train),
            "cooccurrence_drag": cooccurrence_drag(train),
        }
        components["multi_condition_equal"] = sum(zscore(value) for value in components.values())
        actual = y[i]
        current_picks = {name: top_pick(score) for name, score in components.items()}
        for window in (60, 120):
            rates = {name: rolling_rate(hits[name], window) for name in base_names}
            best_name = max(base_names, key=lambda name: (rates[name], name))
            current_picks[f"rolling_best_{window}"] = current_picks[best_name]
            votes = Counter()
            for name in base_names:
                votes[current_picks[name]] += float(np.exp(12 * (rates[name] - 6 / N)))
            current_picks[f"performance_vote_{window}"] = max(votes, key=lambda number: (votes[number], -number))
        for name in names:
            pick = current_picks[name]
            picks[name].append(pick)
            hits[name].append(int(actual[pick - 1]))
    baseline = 6 / N
    dev_size = len(draws) - start - HOLDOUT
    methods = {}
    for name in names:
        values = hits[name]
        methods[name] = {
            "development": {"rounds": dev_size, "hits": sum(values[:dev_size]), "rate": round(float(np.mean(values[:dev_size])), 6)},
            "holdout": {"rounds": HOLDOUT, "hits": sum(values[-HOLDOUT:]), "rate": round(float(np.mean(values[-HOLDOUT:])), 6)},
            "recent": {
                "20": sum(values[-20:]),
                "60": sum(values[-60:]),
                "120": sum(values[-120:]),
            },
            "latest_pick": picks[name][-1],
            "accepted": float(np.mean(values[:dev_size])) > baseline and float(np.mean(values[-HOLDOUT:])) > baseline,
        }
    payload = {
        "rule": "每期只讀當期以前資料；開發段與最後520期保留段分離；兩段都勝過6/49才可進正式模型",
        "official_draws": len(draws),
        "evaluated_rounds": len(draws) - start,
        "development_rounds": dev_size,
        "holdout_rounds": HOLDOUT,
        "random_rate": round(baseline, 6),
        "methods": methods,
        "accepted_methods": [name for name, result in methods.items() if result["accepted"]],
        "conclusion": "沒有任何受測軌跡、週期或拖牌方法同時通過兩段驗證" if not any(result["accepted"] for result in methods.values()) else "僅列出的通過方法可進下一階段，不代表未來保證",
    }
    return payload


def main() -> None:
    payload = build_audit()
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if hasattr(sys.stdout, "buffer"):
        sys.stdout.buffer.write(text.encode("utf-8") + b"\n")
    else:
        print(text)


if __name__ == "__main__":
    main()
