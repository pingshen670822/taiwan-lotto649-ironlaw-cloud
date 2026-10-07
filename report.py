from __future__ import annotations

import hashlib
import html
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from engine import ROOT


REPORTS = ROOT / "reports"
SITE = ROOT / "site"
DOCS = ROOT / "docs"
MOBILE = ROOT / "mobile_cloud"
DOCS_MOBILE = DOCS / "mobile_cloud"
DESTINATIONS = (REPORTS, SITE, DOCS, MOBILE, DOCS_MOBILE)
TAIPEI = ZoneInfo("Asia/Taipei")


def e(value):
    return html.escape(str(value))


def balls(numbers, kind=""):
    return " ".join(f'<span class="ball {kind}">{int(number):02d}</span>' for number in numbers)


def table(head, rows, empty="目前沒有已結算資料"):
    body = "".join("<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows)
    if not body:
        body = f'<tr><td colspan="{len(head)}">{empty}</td></tr>'
    return '<div class="scroll"><table><thead><tr>' + "".join(f"<th>{e(x)}</th>" for x in head) + f"</tr></thead><tbody>{body}</tbody></table></div>"


def settled_rows(history):
    rows = []
    for prediction in reversed(history):
        if prediction.get("status") != "settled":
            continue
        settlement = prediction["settlement"]
        actual = prediction["actual"]
        hit_numbers = "、".join(map(str, settlement["pack_hits"]["九中三"]["numbers"])) or "—"
        rows.append([
            e(prediction["target_date"]),
            e(prediction.get("revision_no", 1)),
            balls(prediction["packs"]["九中三"]),
            balls(actual["main"], "gold") + f'<span class="special">特 {actual["special"]:02d}</span>',
            e(settlement["pack_hits"]["九中三"]["count"]),
            e(hit_numbers),
            "命中" if settlement["special_hit"] else "未中",
        ])
    return rows


def monthly_rows(history):
    grouped = defaultdict(list)
    for prediction in history:
        if prediction.get("status") == "settled":
            grouped[prediction["target_date"][:7]].append(prediction)
    rows = []
    for month, items in sorted(grouped.items(), reverse=True):
        hits = [item["settlement"]["pack_hits"]["九中三"]["count"] for item in items]
        rows.append([month, len(items), sum(hits), f"{sum(hits) / len(hits):.2f}", max(hits), sum(item["settlement"]["special_hit"] for item in items)])
    return rows


def build_reports(analysis_data, history):
    for base in DESTINATIONS:
        base.mkdir(parents=True, exist_ok=True)

    a = analysis_data
    backtest = a["backtest"]
    candidate_rows = [[item["rank"], balls([item["number"]], "sub"), f'{item["probability"] * 100:.3f}%'] for item in a["main_rank"][:18]]
    model_rows = [[
        e(name),
        f'{backtest["main"]["weights"][name] * 100:.2f}%',
        f'{backtest["main"]["weight_diagnostics"][name]["hit20"]:.3f}',
        f'{backtest["main"]["weight_diagnostics"][name]["hit60"]:.3f}',
        f'{backtest["main"]["weight_diagnostics"][name]["hit120"]:.3f}',
        f'{backtest["main"]["weight_diagnostics"][name]["spill20"]:.3f}',
        backtest["main"]["weight_diagnostics"][name]["zero_hit_streak"],
        f'{backtest["main"]["model_logloss"][name]:.6f}',
    ] for name in backtest["main"]["names"]]
    module_review_rows = [[
        e(item["model"]),
        balls([item["top1"]]),
        "命中" if item["top1_hit"] else "未中",
        item["hit_count"],
        balls(item["hit_numbers"], "gold") if item["hit_numbers"] else "—",
        f'{item["weight_before"] * 100:.2f}% → {item["weight_after"] * 100:.2f}%',
        e(item["action"]),
    ] for item in a.get("module_review", {}).get("main", [])]
    special_review_rows = [[
        e(item["model"]),
        balls([item["top1"]], "specialball"),
        "命中" if item["top1_hit"] else "未中",
        item["hit_count"],
        f'{item["weight_before"] * 100:.2f}% → {item["weight_after"] * 100:.2f}%',
        e(item["action"]),
    ] for item in a.get("module_review", {}).get("special", [])]
    boundary_rows = [[
        item["date"],
        item["top9_hits"],
        item["spill_10_15"],
        "、".join(map(str, item["actual_ranks"])),
        item.get("boundary_rotation", {}).get("count", 0),
        balls(item.get("boundary_rotation", {}).get("promoted", [])) if item.get("boundary_rotation", {}).get("promoted") else "—",
        balls(item.get("boundary_rotation", {}).get("demoted", []), "avoid") if item.get("boundary_rotation", {}).get("demoted") else "—",
    ] for item in a.get("rank_boundary_audit", {}).get("latest_rows", [])]
    next_rotation = a.get("rank_boundary_audit", {}).get("next_boundary_rotation", {})
    if next_rotation.get("count", 0):
        rotation_card = f'<article class="pack"><div class="pack-title">本期前9精準壓縮</div><p>升入 {balls(next_rotation.get("promoted", []))}　撤出 {balls(next_rotation.get("demoted", []), "avoid")}</p><small>固定邊界4席；上期號在前9最多3席</small></article>'
    else:
        rotation_card = '<article class="pack"><div class="pack-title">本期前9精準壓縮</div><p>維持前9排序；上期號在前9最多3席。</p></article>'

    research = a.get("research_review", {})
    performance = research.get("performance", {})
    research_rows = [[e(item.get("site")), e("、".join(item.get("methods", []))), f'<a href="{e(item.get("url", ""))}" target="_blank" rel="noopener">來源</a>'] for item in research.get("sources", [])]
    strongest = a.get("strongest_recommendation", {})
    strong_number = strongest.get("number", a["packs"]["最強單支"][0])
    strong_evidence = f'{strongest.get("model_top9_support", "—")}/{strongest.get("model_count", "—")}個模型列前9；{strongest.get("model_top15_support", "—")}/{strongest.get("model_count", "—")}個模型列前15；1040期前9平均{backtest["main"]["avg_hits"]}，隨機基準{a["release_gate"]["main_random_hits"]}'
    low_rows = []
    for prediction in reversed(history):
        if prediction.get("status") != "settled":
            continue
        errors = prediction["settlement"]["avoid_errors"]["十不中"]
        low_rows.append([e(prediction["target_date"]), balls(prediction["avoid"]["十不中"], "avoid"), balls(errors, "gold") if errors else "—", len(errors), "誤開號解除暫避" if errors else "守住"])

    rendered_at = datetime.now(TAIPEI)
    rendered_iso = rendered_at.isoformat(timespec="seconds")
    rendered_display = rendered_at.strftime("%Y-%m-%d %H:%M:%S")
    generated_display = str(a.get("generated_at", "—")).replace("T", " ")
    integrity = a.get("calculation_integrity", {})
    settled_history = [prediction for prediction in history if prediction.get("status") == "settled"]
    latest_settled = settled_history[-1] if settled_history else None
    latest_revision_by_date = {}
    for prediction in settled_history:
        current = latest_revision_by_date.get(prediction["target_date"])
        if current is None or prediction.get("revision_no", 1) >= current.get("revision_no", 1):
            latest_revision_by_date[prediction["target_date"]] = prediction
    sealed_single_rows = list(latest_revision_by_date.values())
    sealed_single_hits = sum(item["settlement"]["pack_hits"]["最強單支"]["count"] for item in sealed_single_rows)
    sealed_single_rate = sealed_single_hits / len(sealed_single_rows) if sealed_single_rows else 0
    latest_rank_audit = a.get("rank_boundary_audit", {}).get("latest_rows", [{}])[-1]
    actual_ranks = latest_rank_audit.get("actual_ranks", [])
    last_rank_result = f'{sum(rank <= 5 for rank in actual_ranks)} / {sum(rank <= 10 for rank in actual_ranks)} / {sum(rank <= 15 for rank in actual_ranks)}'
    last_actual_balls = balls(latest_settled["actual"]["main"], "actual") if latest_settled else "尚無結算"
    strong_probability = next((item["probability"] for item in a["main_rank"] if item["number"] == strong_number), a["main_rank"][0]["probability"])
    rank_evidence = strongest.get("model_rank_evidence", {})
    weighted_support = sum(backtest["main"]["weights"].get(name, 0) for name, rank in rank_evidence.items() if rank <= 9) * 100
    selector = backtest["main"].get("single_selector", {})
    confidence_ok = strongest.get("all_passed") is True and a.get("release_gate", {}).get("passed") is True
    confidence_label = strongest.get("label", "每日多條件最強獨隻" if confidence_ok else "每日相對最強獨隻・未達超強門檻")
    confidence_title = "終極獨隻 1中1" if confidence_ok else "每日最強獨隻（未達超強門檻）"
    check_labels = {
        "independent_single_selector": "獨支使用獨立1中1選擇器",
        "dual_520_windows_complete": "開發520期與保留520期完整",
        "development_520_beats_random": "較舊520期開發段勝過隨機",
        "holdout_520_beats_random": "最新520期保留段勝過隨機",
        "failed_repeat_cooldown": "上期獨支未中禁止同號原地連任",
        "recent_60_not_below_random": "近60期獨支命中率不低於隨機",
        "recent_120_not_below_random": "近120期獨支命中率不低於隨機",
        "at_least_half_models_top9": "至少半數模型列入前9",
        "at_least_two_thirds_models_top15": "至少三分之二模型列入前15",
        "pattern_methods_pass_both_windows": "至少一項軌跡／拖牌規則通過雙區段",
        "release_gate_passed": "正式發布守門通過",
        "previous_draw_used_as_direct_pick": "禁止直接照抄上期開獎號",
    }
    check_rows = [[e(check_labels.get(name, name)), "通過" if (not passed if name == "previous_draw_used_as_direct_pick" else passed) else "未通過"] for name, passed in strongest.get("logic_checks", {}).items()]
    tiers = {
        "A級・唯一最強排序": [strong_number],
        "B級・前三排序": [item["number"] for item in a["main_rank"][:3]],
        "C級・核心前九": a["packs"]["九中三"],
        "D級・次高防守": [item["number"] for item in a["main_rank"][9:18]],
        "E級・低機率暫避": a["avoid"]["十不中"],
    }
    source_rows = [[e(item.get("site", "—")), e("、".join(item.get("methods", []))), f'<a href="{e(item.get("url", ""))}" target="_blank" rel="noopener">來源</a>'] for item in research.get("sources", [])]
    pattern = a.get("pattern_audit", {})
    pattern_labels = {"frequency_60":"60期頻率","frequency_240":"240期頻率","weekday_cycle":"開獎星期週期","transition_drag":"一階拖牌","two_step_drag":"二階拖牌","neighbor_track":"鄰號軌跡","interval_recurrence":"間隔復現","cooccurrence_drag":"共現拖牌","multi_condition_equal":"多條件等權","rolling_best_60":"近60期最佳規則切換","rolling_best_120":"近120期最佳規則切換","performance_vote_60":"近60期績效投票","performance_vote_120":"近120期績效投票"}
    pattern_rows = [[pattern_labels.get(name, name), f'{item.get("development", {}).get("hits", "—")}/{item.get("development", {}).get("rounds", "—")}（{item.get("development", {}).get("rate", 0)*100:.2f}%）', f'{item.get("holdout", {}).get("hits", "—")}/{item.get("holdout", {}).get("rounds", "—")}（{item.get("holdout", {}).get("rate", 0)*100:.2f}%）', f'{item.get("recent", {}).get("20", "—")}／{item.get("recent", {}).get("60", "—")}／{item.get("recent", {}).get("120", "—")}', item.get("latest_pick", "—"), "通過" if item.get("accepted") else "淘汰"] for name, item in pattern.get("methods", {}).items()]
    analysis_text = json.dumps(a, ensure_ascii=False, indent=2)
    version = {
        "updated_at": rendered_iso,
        "latest_period": a["latest_draw"]["period"],
        "hash": hashlib.sha256(analysis_text.encode()).hexdigest()[:16],
    }

    html_text = f'''<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate"><meta http-equiv="Pragma" content="no-cache"><meta http-equiv="Expires" content="0"><meta name="theme-color" content="#7f1017"><meta name="report-version" content="{version['hash']}"><meta name="report-generated-at" content="{e(rendered_iso)}"><link rel="manifest" href="manifest.webmanifest"><link rel="stylesheet" href="style.css"><title>台灣大樂透・本期戰報</title></head>
<body><main>
<header><h1>台灣大樂透・本期戰報</h1><div>{integrity.get('official_rows', a['history']['count']):,}期歷史基底・獨支1040期雙區段驗證・資料更新與模型守門雙軌分離</div>
<div class="app-actions"><button type="button" id="manual-refresh" class="cloud-button update-button">手動更新最新</button><button type="button" id="emergency-repair" class="cloud-button repair-button">當機立即修復</button></div>
<div class="cloud-control-note">按下手動更新後，完成時間會固定顯示並保留；當機修復會清除失效快取、核對四項雲端資料並重新連接。</div>
<div class="update-times"><span>雲端戰報產生：<strong id="cloud-generated-time">{rendered_display}</strong></span><span>最後手動更新完成：<strong id="last-manual-update" data-manual-time>尚未手動更新</strong></span><span>最後立即修復完成：<strong id="last-repair-time" data-repair-time>尚未執行</strong></span></div>
<div id="cloud-action-status" class="cloud-action-status" role="status" aria-live="polite">目前資料：{e(a['latest_draw']['date'])}／第 {e(a['latest_draw']['period'])} 期</div><a id="cloud-repair-center" class="repair-center" href="https://github.com/pingshen670822/taiwan-lotto649-ironlaw-cloud/actions/workflows/cloud-watchdog.yml" target="_blank" rel="noopener">開啟雲端修復中心</a></header>
<nav aria-label="戰報分類">{''.join(f'<button type="button" data-tab="{tab}">{label}</button>' for tab, label in [('decision','本期預測'),('models','回測驗證'),('review','開獎檢討'),('monthly','歷史封存'),('verify','模型說明'),('iron','系統健康')])}</nav>

<section id="decision" class="tab active">
<div class="band strong {'approved' if confidence_ok else 'warning'}"><div class="badge">{confidence_label}</div><h2>{confidence_title}</h2><div class="number">{strong_number:02d}</div><div class="validation-seals"><span>開發520期 {strongest.get('development', {}).get('hits', '—')}/{strongest.get('development', {}).get('rounds', '—')}（{strongest.get('development', {}).get('rate', 0) * 100:.2f}%）</span><span>保留520期 {strongest.get('holdout', {}).get('hits', '—')}/{strongest.get('holdout', {}).get('rounds', '—')}（{strongest.get('holdout', {}).get('rate', 0) * 100:.2f}%）</span><span>隨機基準 {strongest.get('random_hit_rate', 6 / 49) * 100:.3f}%</span><span>近20／60／120期 {strongest.get('recent_hits', {}).get('20', '—')}／{strongest.get('recent_hits', {}).get('60', '—')}／{strongest.get('recent_hits', {}).get('120', '—')}中</span><span>模型前9支持 {strongest.get('model_top9_support', '—')}/{strongest.get('model_count', '—')}</span></div><p><b>{e(strongest.get('warning', '此為系統內相對排序，不代表中獎保證。'))}</b></p><p class="note">1040期合計 {strongest.get('walk_forward_hits', '—')}/{strongest.get('walk_forward_rounds', '—')}（{strongest.get('walk_forward_hit_rate', 0) * 100:.2f}%）；正式封存實戰 {sealed_single_hits}/{len(sealed_single_rows)}（{sealed_single_rate * 100:.2f}%）。三種數字分開揭露，不得混用或宣稱90%。</p></div>
<div class="band ironlaw-numbers"><h2>本期其他鐵律號碼</h2>{table(['類型','正式號碼'], [[e(name), balls(numbers)] for name, numbers in a['packs'].items()])}<h3>特別號獨立運算</h3>{table(['類型','正式號碼'], [[e(name), balls(numbers, 'specialball')] for name, numbers in a['special_packs'].items()])}<p class="note">所有號碼均由同一次正式運算產生；最新開獎資料同步與模型信心守門分開執行。</p></div>
<details class="report-details"><summary>查看獨支強烈驗證與完整運算</summary><div class="band"><h2>多項邏輯驗證</h2>{table(['檢查項目','結果'], check_rows)}</div><div class="band"><h2>清楚分層推薦</h2><div class="grid">{''.join(f'<div class="card {"primary" if index == 0 else "low" if index == 4 else ""}"><div class="label">{e(name)}</div><div class="number-line">{balls(numbers, "avoid" if index == 4 else "")}</div></div>' for index, (name, numbers) in enumerate(tiers.items()))}</div></div></details>
<details class="report-details"><summary>查看資料、排名與完整牌組</summary><div class="band"><h2>本期資料</h2><div class="grid"><div class="card"><div class="label">預測目標日</div><div class="value">{e(a['target_date'])}</div></div><div class="card"><div class="label">歷史資料截止日</div><div class="value">{e(a['latest_draw']['date'])}</div></div><div class="card"><div class="label">依據期別</div><div class="value">第 {e(a['latest_draw']['period'])} 期</div></div><div class="card"><div class="label">使用歷史期數</div><div class="value">{integrity.get('official_rows', a['history']['count']):,}期</div></div><div class="card"><div class="label">戰報產生時間</div><div class="value">{rendered_display}</div></div></div></div><div class="band"><h2>內部前18名診斷</h2><p class="note">此表為運算順位與校準值，不代表中獎保證。</p>{table(['順位','號碼','校準機率'], candidate_rows)}</div><div class="band"><h2>八組結構平衡建議</h2><div class="grid">{''.join(f'<div class="card"><div class="label">第{index + 1}組</div><div class="number-line">{balls(group)}</div></div>' for index, group in enumerate(a['suggested_sets']))}</div></div><div class="band warning"><h2>下期低機率暫避</h2><p class="note">只做風險排序，不代表絕對不開；與主攻牌完全分離。</p><div class="grid">{''.join(f'<div class="card low"><div class="label">{e(name)}</div><div class="number-line">{balls(numbers, "avoid")}</div></div>' for name, numbers in a['avoid'].items())}</div></div></details>
</section>

<section id="models" class="tab"><div class="band"><h2>獨立1中1與前9碼走步回測</h2><div class="grid"><div class="card primary"><div class="label">獨支較舊520期開發段</div><div class="value">{selector.get('development', {}).get('hits', '—')}/520（{selector.get('development', {}).get('rate', 0) * 100:.2f}%）</div><div class="note">必須勝過隨機 {selector.get('random_rate', 6 / 49) * 100:.3f}%</div></div><div class="card primary"><div class="label">獨支最新520期保留段</div><div class="value">{selector.get('holdout', {}).get('hits', '—')}/520（{selector.get('holdout', {}).get('rate', 0) * 100:.2f}%）</div><div class="note">禁止只挑這段漂亮數字</div></div><div class="card"><div class="label">獨支近20／60／120期</div><div class="value">{selector.get('recent_hits', {}).get('20', '—')}／{selector.get('recent_hits', {}).get('60', '—')}／{selector.get('recent_hits', {}).get('120', '—')}中</div><div class="note">只使用各期開獎前資料</div></div><div class="card"><div class="label">前9碼1040期平均命中</div><div class="value">{backtest['main']['avg_hits']}</div><div class="note">隨機基準 {a['release_gate']['main_random_hits']}</div></div><div class="card"><div class="label">最近20期前9平均命中</div><div class="value">{performance.get('v9_recent20', '—')}</div><div class="note">短期表現獨立監控</div></div><div class="card"><div class="label">第10–15名平均外溢</div><div class="value">{backtest['main']['avg_spill_10_15']}</div><div class="note">外溢不算前9成功</div></div></div></div><div class="band"><h2>軌跡、週期與拖牌雙區段稽核</h2><p><b>{e(pattern.get('conclusion', '尚未產生稽核結果'))}</b></p><p class="note">准入規則：較舊520期與最新520期必須同時高於6/49（12.2449%）；只在近期漂亮的一律淘汰。</p>{table(['受測規則','開發520期','保留520期','近20／60／120中','下期候選','結果'], pattern_rows)}</div><div class="band"><h2>第10名後問題專項檢測</h2>{rotation_card}{table(['開獎日','前9命中','第10–15名外溢','六顆實開順位','撤換數','升入前9','撤出前9'], boundary_rows)}</div><details class="report-details"><summary>查看逐模組錯誤檢討與滾動調整</summary><div class="band"><h2>前9碼三層滾動權重</h2>{table(['模型','最終權重','近20前9命中','近60前9命中','近120前9命中','近20外溢','連續零命中','對數損失'], model_rows)}</div><div class="band"><h2>主號模型錯誤追責</h2>{table(['模型','原第一名','1中1','前9命中','命中號','權重變動','處置'], module_review_rows)}</div><div class="band"><h2>特別號模型錯誤追責</h2>{table(['模型','原第一名','1中1','前三命中','權重變動','處置'], special_review_rows)}</div></details></section>

<section id="review" class="tab"><div class="band"><h2>預測對實際逐期驗算</h2><p>預測先封存，開獎後只結算，禁止回改舊牌；第10名後命中不算前9成功。</p>{table(['目標日','版本','原前9碼','實際開獎','前9命中','命中號','特別號'], settled_rows(history))}</div><div class="band warning"><h2>最新一期錯誤檢討</h2><p>上期實開：{last_actual_balls}</p><p><b>Top5／Top10／Top15 命中＝{last_rank_result}</b></p><p><b>封存獨支實戰＝{sealed_single_hits}/{len(sealed_single_rows)}（{sealed_single_rate * 100:.2f}%）；上期獨支 {latest_settled['packs']['最強單支'][0] if latest_settled else '—'} {'命中' if latest_settled and latest_settled['settlement']['pack_hits']['最強單支']['count'] else '未中'}。</b></p><ul><li>現行獨支最新520期為77中，但較舊520期只有54中，因此禁止單獨拿77/520冒充穩定能力。</li><li>星期、拖牌、鄰號、間隔與共現軌跡必須雙區段都勝過隨機，否則不進正式核心。</li><li>第10–15名外溢一律列失敗並扣除責任模型權重。</li><li>每期重算20／60／120期權重、連續失準與穩定度懲罰。</li><li>模型守門不足只降級標示，永遠不得阻擋最新官方資料同步。</li></ul></div><details class="report-details"><summary>查看低機率號碼誤開檢討</summary><div class="band">{table(['目標日','原十不中','誤開號','顆數','修正'], low_rows)}</div></details></section>

<section id="monthly" class="tab"><div class="band"><h2>歷史封存與每月總整理</h2><p>每一筆依開獎前的目標日與版本獨立封存，不合併、不回改。</p>{table(['月份','封存結算筆數','總命中','平均命中','單筆最高','特別號命中'], monthly_rows(history))}</div></section>

<section id="verify" class="tab"><div class="band"><h2>模型說明</h2><p>前9模型依20／60／120期成績、外溢、穩定度與連續失誤配權，再以75%校準機率與25%跨模型順位融合；唯一獨支另用歷史1中1成績配權、模型第一名投票及整體機率排序融合，並強制接受較舊520期開發段與最新520期保留段雙重驗證。所有路徑只使用開獎前資料。</p><h3>失敗回饋規則</h3><ul><li>獨支與前9分開結算，不再用前9共識冒充1中1準確度</li><li>不得只展示近期漂亮區段，兩個520期必須同時揭露</li><li>每期檢查六顆實際主號的預測名次</li><li>命中落到第10名後即列入錯誤檢討</li><li>短期20期占50%，60期占30%，120期占20%</li><li>外溢、校準誤差、連續零命中及不穩定會扣權</li><li>單一模型權重上限25%</li><li>未通過守門禁止假標高信心，但最新開獎資料仍必須同步</li></ul></div><div class="band"><h2>外部分析模式研究</h2>{table(['來源','分析模式','連結'], source_rows)}<h3>通過走步驗證後採用</h3><ul>{''.join(f'<li>{e(item)}</li>' for item in research.get('accepted', []))}</ul><h3>驗證後拒絕進入正式預測</h3><ul>{''.join(f'<li>{e(item)}</li>' for item in research.get('rejected_after_walk_forward', []))}</ul></div></section>

<section id="iron" class="tab"><div class="band"><h2>系統健康</h2><div class="grid"><div class="card"><div class="label">資料日期／期別</div><div class="value">{e(a['latest_draw']['date'])}／第 {e(a['latest_draw']['period'])} 期</div></div><div class="card"><div class="label">預測守門</div><div class="value {'ok' if a['release_gate']['passed'] else 'bad'}">{'已通過' if a['release_gate']['passed'] else '未通過'}</div></div><div class="card"><div class="label">雲端主更新</div><div class="value ok">每3小時＋開獎後密集檢查</div></div><div class="card"><div class="label">第二層自主修復</div><div class="value ok">逾時或失敗自動啟動</div></div></div></div><div class="band"><h2>台灣大樂透規則核對</h2><p>每期由1至49開出6個主號，另開1個特別號；資料必須同時通過期別、日期、六顆主號與特別號完整性驗證。</p></div><div class="band"><h2>鐵律守門</h2><ol><li>只准使用官方開獎資料，禁止假資料與未來資料</li><li>預測封存後不可因開獎結果修改</li><li>開獎後先結算，再檢討所有模型，最後全量重算</li><li>回測嚴格按時間順序，禁止偷看未來</li><li>軌跡與拖牌規則必須通過開發／保留雙區段，禁止挑漂亮區段</li><li>主攻、暫避、開獎檢討、歷史封存分區顯示</li><li>抓號失敗保留最後有效版本，絕不以不完整資料覆寫</li><li>開獎後2小時未更新，立即啟動自主修復並持續重試</li></ol><h3>生命週期</h3><p>官方資料 → 完整性驗證 → 上期結算 → 模組追責 → 1040期雙區段重測 → 軌跡規律稽核 → 健康檢查 → 戰報生成 → 手機同步 → 失敗時第二層救援</p><p><a href="integrity_audit.json" target="_blank" rel="noopener">每日完整性稽核</a>　<a href="single_pattern_audit.json" target="_blank" rel="noopener">獨支軌跡規律稽核</a>　<a href="self_test_report.json" target="_blank" rel="noopener">全系統自測</a></p></div></section>

<div class="band warning notice">{e(a['notice'])}</div><footer>資料基準 {e(a['latest_draw']['date'])}・目標 {e(a['target_date'])}・核心 {e(a['engine'])}・頁面版本 {rendered_display}</footer>
</main><script src="app.js"></script></body></html>'''

    css = '''*{box-sizing:border-box}[hidden]{display:none!important}html,body{max-width:100%;overflow-x:hidden}body{margin:0;background:#f3f4f6;color:#172033;font-family:system-ui,"Microsoft JhengHei",sans-serif;line-height:1.55}main{max-width:1180px;margin:auto;padding:18px}header{background:linear-gradient(135deg,#7f1017,#d1242f);color:#fff;padding:24px;border-radius:14px}h1{margin:0 0 5px;font-size:28px}h2{border-left:6px solid #c1121f;padding-left:10px;color:#7f1017;margin:0 0 16px}h3{color:#7f1017;margin:24px 0 10px}p{overflow-wrap:anywhere}a{color:#7f1017;font-weight:800}nav{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:8px;margin:14px 0}nav button{display:flex;align-items:center;justify-content:center;min-height:44px;background:#fff;border:1px solid #d1d5db;border-radius:9px;padding:8px;color:#7f1017;font:800 15px system-ui,"Microsoft JhengHei",sans-serif;text-align:center;cursor:pointer}nav button.on{background:#7f1017;color:#fff;border-color:#7f1017}.tab{display:none}.tab.active{display:block}.app-actions{display:flex;align-items:center;gap:9px;flex-wrap:wrap;margin-top:14px}.cloud-button{min-height:44px;border:2px solid #fff;border-radius:999px;padding:8px 18px;color:#fff;font:900 16px system-ui,"Microsoft JhengHei",sans-serif;box-shadow:0 3px 10px #0004;cursor:pointer}.cloud-button:focus-visible{outline:3px solid #fff;outline-offset:3px}.cloud-button:disabled{opacity:.62;cursor:wait}.update-button{background:#087348}.repair-button{background:#651018}.cloud-control-note{margin-top:12px;font-weight:700}.update-times{display:flex;gap:8px 20px;flex-wrap:wrap;margin-top:10px;padding:10px 12px;border-radius:10px;background:#ffffff20}.cloud-action-status{min-height:24px;margin-top:8px;font-weight:800}.cloud-action-status.ok,.cloud-action-status.success{color:#d8ffe9}.cloud-action-status.error,.cloud-action-status.failure{color:#fff0a8}.cloud-action-status.working{color:#fff3bd}.repair-center{display:none;color:#fff7bc;margin-top:8px}.repair-center.show{display:inline-block}.band{background:#fff;border:1px solid #d8dee8;border-radius:12px;padding:18px;margin:14px 0;box-shadow:0 2px 8px #0000000d}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:10px}.card{border:1px solid #d9dde5;border-radius:10px;padding:13px;background:#fff;min-width:0}.primary{border:2px solid #c1121f;background:#fff5f5}.strong{border:3px solid #b8860b;background:linear-gradient(135deg,#fff8d8,#fff);box-shadow:0 4px 18px #b8860b33}.strong.warning{border-color:#e9b949}.strong .number{font-size:64px}.badge{display:inline-block;padding:6px 12px;border-radius:999px;background:#7f1017;color:#fff;font-weight:900;margin-bottom:8px}.label{color:#687386;font-size:13px}.value{font-size:18px;font-weight:800;margin-top:4px}.number{color:#c1121f;font-size:38px;font-weight:900;letter-spacing:2px}.number-line{font-size:20px;letter-spacing:2px;color:#7f1017}.validation-seals{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0}.validation-seals span{border:1px solid #d3b358;border-radius:999px;padding:5px 10px;background:#fff;font-weight:800}.scroll,.table-wrap{overflow-x:auto;max-width:100%}table{width:100%;border-collapse:collapse}th{background:#7f1017;color:#fff}th,td{padding:9px;border:1px solid #d7dce4;text-align:left;white-space:nowrap}tr:nth-child(even) td{background:#fafafa}.warning{background:#fff8e6;border-color:#e9b949}.ok{color:#176b3a}.bad{color:#9b1c1c}.note{color:#626d7d}.low{border-color:#596576}.ball{display:inline-grid;place-items:center;width:39px;height:39px;margin:3px;border-radius:50%;background:#a71c23;color:#fff;font-weight:900;letter-spacing:0}.ball.sub{border:2px solid #d58b91;background:#fff;color:#7f1017}.actual{background:#087348}.avoid{background:#596576}.specialball,.special{background:#d08a00}.special{display:inline-block;padding:8px;color:#fff;border-radius:10px;margin-left:4px}.report-details{margin:14px 0;border:1px solid #d8dee8;border-radius:12px;background:#fff;box-shadow:0 2px 8px #0000000d}.report-details>summary{list-style:none;min-height:54px;padding:14px 18px;display:flex;align-items:center;justify-content:space-between;color:#7f1017;font-weight:900;cursor:pointer}.report-details>summary::-webkit-details-marker{display:none}.report-details>summary::after{content:"點開";padding:5px 10px;border-radius:999px;background:#7f1017;color:#fff;font-size:13px}.report-details[open]>summary::after{content:"收起"}.report-details>.band{margin:0;border-width:1px 0 0;border-radius:0;box-shadow:none}.notice{text-align:center}footer{padding:14px 4px 28px;color:#687386;font-size:13px}@media(max-width:760px){main{padding:8px}header{border-radius:8px;padding:19px}nav{grid-template-columns:repeat(3,minmax(0,1fr))}nav button{font-size:14px}.band{padding:13px}h1{font-size:24px}.number-line{font-size:18px;letter-spacing:1px}.strong .number{font-size:56px}.cloud-button{width:100%}.ball{width:35px;height:35px}th,td{font-size:14px}.update-times{display:grid}}@media(max-width:390px){nav{grid-template-columns:repeat(2,minmax(0,1fr))}}'''

    js = '''document.querySelectorAll('nav button').forEach((button,index)=>{if(!index)button.classList.add('on');button.addEventListener('click',()=>{document.querySelectorAll('.tab').forEach(tab=>tab.classList.remove('active'));document.querySelectorAll('nav button').forEach(item=>item.classList.remove('on'));document.getElementById(button.dataset.tab)?.classList.add('active');button.classList.add('on');window.scrollTo({top:0,behavior:'smooth'});})});
const pageHash=document.querySelector('meta[name="report-version"]')?.content||'';
const updateButton=document.getElementById('manual-refresh');
const repairButton=document.getElementById('emergency-repair');
const cloudStatus=document.getElementById('cloud-action-status');
const repairCenter=document.getElementById('cloud-repair-center');
let cloudActionRunning=false;
function setCloudStatus(message,state=''){cloudStatus.textContent=message;cloudStatus.className='cloud-action-status'+(state?' '+state:'');}
function setCloudBusy(busy){cloudActionRunning=busy;updateButton.disabled=busy;repairButton.disabled=busy;}
function freshUrl(path){return path+(path.includes('?')?'&':'?')+'t='+Date.now();}
function pause(ms){return new Promise(resolve=>setTimeout(resolve,ms));}
function taipeiNow(){const parts=new Intl.DateTimeFormat('zh-TW',{timeZone:'Asia/Taipei',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'}).formatToParts(new Date());const p=Object.fromEntries(parts.map(x=>[x.type,x.value]));return p.year+'-'+p.month+'-'+p.day+' '+p.hour+':'+p.minute+':'+p.second;}
function showStoredTimes(){const manual=localStorage.getItem('tw649-manual-update-time')||'尚未手動更新';const repair=localStorage.getItem('tw649-repair-time')||'尚未執行';document.querySelectorAll('[data-manual-time]').forEach(x=>x.textContent=manual);document.querySelectorAll('[data-repair-time]').forEach(x=>x.textContent=repair);}
async function fetchJson(path,timeout=10000){const controller=new AbortController();const timer=setTimeout(()=>controller.abort(),timeout);try{const response=await fetch(freshUrl(path),{cache:'no-store',headers:{'Cache-Control':'no-cache'},signal:controller.signal});if(!response.ok)throw new Error(path+' HTTP '+response.status);return await response.json();}finally{clearTimeout(timer);}}
function forceFreshReload(reason){const url=new URL(location.href);url.searchParams.set('cloud_refresh',Date.now());url.searchParams.set('reason',reason);location.replace(url.toString());}
function validateCloudBundle(version,analysis,selfTest,repairStatus){if(!version.hash||!version.latest_period)throw new Error('版本資料不完整');if(String(analysis?.latest_draw?.period)!==String(version.latest_period))throw new Error('期別不同步');if(selfTest.passed!==true)throw new Error('全系統自測未通過');if(!['healthy','awaiting_verification'].includes(repairStatus.status))throw new Error('雲端修復狀態異常');return version;}
async function manualUpdateLatest(){if(cloudActionRunning)return;setCloudBusy(true);repairCenter.classList.remove('show');setCloudStatus('正在跳過快取取得最新戰報…','working');try{const [version,analysis,selfTest]=await Promise.all([fetchJson('version.json'),fetchJson('latest_analysis.json'),fetchJson('self_test_report.json')]);if(!version.hash||String(analysis?.latest_draw?.period)!==String(version.latest_period))throw new Error('雲端期別核對失敗');if(selfTest.passed!==true)throw new Error('雲端全系統檢測未通過');localStorage.setItem('tw649-version',version.hash);const completed=taipeiNow();localStorage.setItem('tw649-manual-update-time',completed);showStoredTimes();if(version.hash!==pageHash){setCloudStatus('發現新版：第 '+version.latest_period+' 期；完成時間 '+completed+'，正在載入。','success');await pause(700);forceFreshReload('manual');return;}setCloudStatus('檢查完成：'+completed+'／第 '+version.latest_period+' 期，目前已是最新，不需重新載入。','success');setCloudBusy(false);}catch(error){setCloudStatus('手動更新失敗：'+error.message+'。請按「當機立即修復」。','failure');setCloudBusy(false);}}
async function clearBrokenClientState(){if('serviceWorker'in navigator){const registrations=await navigator.serviceWorker.getRegistrations();await Promise.all(registrations.map(registration=>registration.unregister()));}if('caches'in window){const keys=await caches.keys();await Promise.all(keys.map(key=>caches.delete(key)));}localStorage.removeItem('tw649-version');}
async function immediateRepair(){if(cloudActionRunning)return;setCloudBusy(true);repairCenter.classList.remove('show');setCloudStatus('正在清除失效快取與背景程式…','working');let lastError=new Error('未知錯誤');try{await clearBrokenClientState();}catch(error){lastError=error;}for(let attempt=1;attempt<=3;attempt++){setCloudStatus('立即修復第 '+attempt+'/3 輪：核對雲端資料…','working');try{const [version,analysis,selfTest,repairStatus]=await Promise.all([fetchJson('version.json'),fetchJson('latest_analysis.json'),fetchJson('self_test_report.json'),fetchJson('self_repair_status.json')]);validateCloudBundle(version,analysis,selfTest,repairStatus);localStorage.setItem('tw649-version',version.hash);const completed=taipeiNow();localStorage.setItem('tw649-repair-time',completed);showStoredTimes();setCloudStatus('修復成功：'+completed+'／第 '+version.latest_period+' 期資料完整，正在重新載入。','success');await pause(1000);forceFreshReload('repair');return;}catch(error){lastError=error;if(attempt<3)await pause(attempt*1200);}}setCloudStatus('三輪修復未通過：'+lastError.message+'。雲端看門狗仍會持續自動修復。','failure');repairCenter.classList.add('show');setCloudBusy(false);}
updateButton.addEventListener('click',manualUpdateLatest);repairButton.addEventListener('click',immediateRepair);
async function refreshVersion(){try{const version=await fetchJson('version.json');const old=localStorage.getItem('tw649-version');if(old&&old!==version.hash){localStorage.setItem('tw649-version',version.hash);forceFreshReload('automatic');}else localStorage.setItem('tw649-version',version.hash);}catch(error){setCloudStatus('雲端版本檢查暫時失敗，可使用立即修復。','failure');}}
showStoredTimes();refreshVersion();setInterval(refreshVersion,60000);document.addEventListener('visibilitychange',()=>{if(!document.hidden)refreshVersion()});window.addEventListener('pageshow',()=>{showStoredTimes();refreshVersion()});if('serviceWorker'in navigator)navigator.serviceWorker.register('service-worker.js').then(registration=>registration.update()).catch(()=>{});'''
    repair_status = json.dumps({
        "system": a["system"],
        "status": "awaiting_verification",
        "checked_at": rendered_iso,
        "repair_deadline": "開獎後120分鐘（台灣時間22:30）",
        "latest_period": a["latest_draw"]["period"],
        "latest_date": a["latest_draw"]["date"],
        "target_date": a["target_date"],
    }, ensure_ascii=False, indent=2)
    manifest = json.dumps({
        "name": "台灣大樂透精算預測戰報",
        "short_name": "大樂透戰報",
        "start_url": "./",
        "display": "standalone",
        "theme_color": "#7f1017",
        "background_color": "#f3f4f6",
    }, ensure_ascii=False)
    service_worker = "const C='tw649-dual-window-v12';self.addEventListener('install',e=>{self.skipWaiting();e.waitUntil(caches.open(C).then(c=>c.addAll(['./','index.html','style.css','app.js'])))});self.addEventListener('activate',e=>e.waitUntil(Promise.all([self.clients.claim(),caches.keys().then(xs=>Promise.all(xs.filter(x=>x!==C).map(x=>caches.delete(x))))])));self.addEventListener('fetch',e=>e.respondWith(fetch(e.request,{cache:'no-store'}).catch(()=>caches.match(e.request))))"

    for base in DESTINATIONS:
        (base / "index.html").write_text(html_text, encoding="utf-8")
        (base / "latest_battle_report.html").write_text(html_text, encoding="utf-8")
        (base / "latest_analysis.json").write_text(analysis_text, encoding="utf-8")
        (base / "single_pattern_audit.json").write_text(json.dumps(pattern, ensure_ascii=False, indent=2), encoding="utf-8")
        (base / "prediction_history.json").write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
        (base / "version.json").write_text(json.dumps(version, ensure_ascii=False, indent=2), encoding="utf-8")
        (base / "self_repair_status.json").write_text(repair_status, encoding="utf-8")
        (base / "style.css").write_text(css, encoding="utf-8")
        (base / "app.js").write_text(js, encoding="utf-8")
        (base / "manifest.webmanifest").write_text(manifest, encoding="utf-8")
        (base / "service-worker.js").write_text(service_worker, encoding="utf-8")
