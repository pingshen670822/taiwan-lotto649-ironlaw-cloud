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
    strong_evidence = f'{strongest.get("model_top9_support", "—")}/{strongest.get("model_count", "—")}個模型列前9；{strongest.get("model_top15_support", "—")}/{strongest.get("model_count", "—")}個模型列前15；520期前9平均{backtest["main"]["avg_hits"]}，高於隨機{a["release_gate"]["main_random_hits"]}'
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
    settled_history = [prediction for prediction in history if prediction.get("status") == "settled"]
    latest_settled = settled_history[-1] if settled_history else None
    if latest_settled:
        last_hit = latest_settled["settlement"]["pack_hits"]["九中三"]["count"]
        last_special = "命中" if latest_settled["settlement"]["special_hit"] else "未中"
        last_result = f"{last_hit} 顆／特別號{last_special}"
    else:
        last_result = "尚無結算"
    integrity = a.get("calculation_integrity", {})
    update_rows = [
        ["資料運算完成", "通過", generated_display, a["latest_draw"]["period"], f'官方資料共 {integrity.get("official_rows", "—")} 期'],
        ["戰報產生完成", "通過", rendered_display, "五端同步", "本機、完整站、Pages及手機版內容一致"],
        ["手機手動更新", "待命", '<span data-manual-time>尚未執行</span>', "跳過快取", "完成後本頁保留可見時間"],
        ["當機立即修復", "待命", '<span data-repair-time>尚未執行</span>', "最多3輪", "未通過不得假報成功"],
    ]

    html_text = f'''<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate"><meta http-equiv="Pragma" content="no-cache"><meta http-equiv="Expires" content="0"><meta name="theme-color" content="#10231e"><link rel="manifest" href="manifest.webmanifest"><link rel="stylesheet" href="style.css"><title>台灣大樂透精算預測戰報</title></head><body>
<header><div class="wrap"><h1>台灣大樂透精算預測戰報</h1><p>最新開獎：第 {a['latest_draw']['period']} 期／{a['latest_draw']['date']}</p><div class="latest-balls ball-line">{balls(a['latest_draw']['main'], 'gold')}<span class="special">特 {a['latest_draw']['special']:02d}</span></div><p>預測目標：{a['target_date']}　運算完成：<time id="calculation-updated-at">{generated_display}</time></p><p>官方歷史資料：{integrity.get('first_date', '2007-01-02')} 至 {a['latest_draw']['date']}<br>共 {integrity.get('official_rows', '—')} 期</p><div class="status"><span class="pill">資料狀態：完整</span><span class="pill">發布門檻：通過</span><span class="pill">本頁產生：{rendered_display}</span><span class="pill">最後手動更新：<span data-manual-time>尚未執行</span></span></div></div></header>
<main><div class="wrap">
<section class="cloud-tools" aria-label="雲端更新與修復"><h2>雲端更新與立即修復</h2><div class="cloud-actions"><button id="manual-update" type="button">↻ 手動更新最新</button><button id="immediate-repair" class="repair-button" type="button">🛠 當機立即修復</button></div><div id="cloud-action-status" class="cloud-status" role="status" aria-live="polite">雲端同步待命</div><div class="action-times"><span>手動更新：<b data-manual-time>尚未執行</b></span><span>立即修復：<b data-repair-time>尚未執行</b></span></div><a id="cloud-repair-center" class="repair-center" href="https://github.com/pingshen670822/taiwan-lotto649-ironlaw-cloud/actions/workflows/cloud-watchdog.yml" target="_blank" rel="noopener">開啟雲端修復中心</a></section>
<section><h2>本期結論</h2><div class="grid"><article class="card"><div class="label">最強獨隻1中1</div><div class="value">{balls([strong_number])}</div><p>{e(strong_evidence)}</p></article><article class="card"><div class="label">前9碼集中防線</div><div class="value">{balls(a['packs']['九中三'])}</div><p>主戰只看這組，逐期以前9碼驗算。</p></article><article class="card"><div class="label">上一期檢討</div><div class="value">{e(last_result)}</div><p>第10–15名外溢另列失敗追責。</p></article></div></section>
<section><h2>9碼核心</h2><div class="core"><div class="ball-line">{balls(a['packs']['九中三'])}</div><p class="sync-check">同步核對：獨隻 {strong_number:02d}／前9 {' '.join(f'{number:02d}' for number in a['packs']['九中三'])}</p><p>主戰集中防線；所有號碼依最新官方資料與520期走步結果重新排序。</p></div></section>
<section><h2>強牌組</h2><div class="grid two"><article class="pack"><div class="pack-title">最強獨隻1中1</div>{balls([strong_number])}<p>本期唯一強推；不代表保證中獎。</p></article>{''.join(f'<article class="pack"><div class="pack-title">{e(key)}</div><div class="ball-line">{balls(values)}</div><p>依最新資料每期重新運算。</p></article>' for key, values in a['packs'].items() if key in ('二中一', '三中一', '五中二'))}</div></section>
<section><h2>特別號獨立運算</h2><div class="grid two">{''.join(f'<article class="pack specialpack"><div class="pack-title">{e(key)}</div><div class="ball-line">{balls(values, "specialball")}</div><p>與主號模型完全分離。</p></article>' for key, values in a['special_packs'].items())}</div></section>
<section><h2>Top18 候選逐號驗算</h2><p>機率經公平開獎先驗收縮，避免把微弱歷史訊號包裝成保證。</p>{table(['順位', '號碼', '校準機率'], candidate_rows)}</section>
<section class="band update-status"><h2>更新執行狀態</h2>{table(['項目', '狀態', '時間', '步驟或版本', '結果說明'], update_rows)}</section>
<section><h2>前9邊界與上期檢討</h2>{rotation_card}<p>每期檢查實開號碼是否落在第9名後；第10–15名一律列外溢失敗並回饋權重。</p>{table(['開獎日', '前9命中', '第10–15名外溢', '六顆實開順位', '撤換數', '升入前9', '撤出前9'], boundary_rows)}<h3>預測對實際逐期驗算</h3>{table(['目標日', '版本', '原前9碼', '實際開獎', '前9命中', '命中號', '特別號'], settled_rows(history))}</section>
<section><h2>低機率暫避</h2><p>只做風險排序，不代表絕對不開；與主攻牌完全分離。</p><div class="grid two">{''.join(f'<article class="pack low"><div class="pack-title">{e(key)}</div>{balls(values, "avoid")}</article>' for key, values in a['avoid'].items())}</div><h3>低機率誤開檢討</h3>{table(['目標日', '原十不中', '誤開號', '顆數', '修正'], low_rows)}</section>
<section><h2>520期模型回測</h2><div class="grid"><article class="card"><div class="label">主號前9平均命中</div><div class="value">{backtest['main']['avg_hits']}</div><p>隨機基準 1.1020</p></article><article class="card"><div class="label">最近20期平均命中</div><div class="value">{performance.get('v6_recent20', '—')}</div><p>逐期滾動檢查</p></article><article class="card"><div class="label">第10–15名平均外溢</div><div class="value">{backtest['main']['avg_spill_10_15']}</div><p>外溢不算成功</p></article></div><h3>前9碼20／60／120期多尺度權重</h3>{table(['模型', '最終權重', '近20前9命中', '近60前9命中', '近120前9命中', '近20外溢', '連續零命中', '對數損失'], model_rows)}<h3>上期主號逐模型錯誤追責</h3>{table(['模型', '原第一名', '1中1', '前9命中', '命中號', '權重變動', '處置'], module_review_rows)}<h3>上期特別號逐模型錯誤追責</h3>{table(['模型', '原第一名', '1中1', '前三命中', '權重變動', '處置'], special_review_rows)}</section>
<section><h2>外部模式研究與實測</h2><p>只有通過520期無未來資料走步測試的方法才能進入正式運算。</p>{table(['研究來源', '分析模式', '連結'], research_rows)}<p><b>保留：</b>{e('、'.join(research.get('accepted', [])))}</p><p><b>回測後拒絕：</b>{e('、'.join(research.get('rejected_after_walk_forward', [])))}</p></section>
<section><h2>八組結構平衡建議</h2><div class="grid two">{''.join(f'<article class="pack"><div class="pack-title">第{index + 1}組</div><div class="ball-line">{balls(values)}</div></article>' for index, values in enumerate(a['suggested_sets']))}</div></section>
<section><h2>每月總整理</h2>{table(['月份', '結算期數', '總命中', '平均命中', '單期最高', '特別號命中'], monthly_rows(history))}</section>
<section><h2>鐵律守門與故障防線</h2><ol><li>官方資料必須通過期別、日期、6主號及特別號完整驗證。</li><li>跨月補抓本月與上月；無新期別不得製造空白提交。</li><li>預測封存後不可用開獎答案回改。</li><li>運算、報表或同步未通過時回復最後有效版本。</li><li>開獎後120分鐘仍未更新，每10分鐘啟動自主修復。</li><li>手機開啟、回到前景及每60秒核對雲端版本。</li><li>新版推送後由獨立看門狗驗證；失敗最多自動重建3次。</li><li>手動更新與立即修復完成後必須顯示台灣時間；未通過不得假報成功。</li></ol><p><a href="integrity_audit.json" target="_blank" rel="noopener">查看每日完整性稽核</a>　<a href="self_test_report.json" target="_blank" rel="noopener">查看全系統自測</a></p></section>
<p class="notice">{e(a['notice'])}</p></div></main><footer><div class="wrap">資料基準 {a['latest_draw']['date']}・目標 {a['target_date']}・頁面產生 {rendered_iso}。本戰報為歷史資料研究與風險排序，不保證開獎命中或獲利。</div></footer><script src="app.js"></script></body></html>'''

    css = ''':root{--ink:#13201b;--muted:#66736d;--line:#d9e3dd;--paper:#f7faf8;--green:#0b7a53;--green-dark:#10231e;--green-soft:#e8f6ef;--gold:#b7791f;--gold-soft:#fff4dc;--red:#b42318;--red-soft:#ffebe8}*{box-sizing:border-box}html,body{max-width:100%;overflow-x:hidden}body{margin:0;background:#fff;color:var(--ink);font-family:"Microsoft JhengHei","Noto Sans TC",Arial,sans-serif;line-height:1.55}.wrap{max-width:1040px;margin:0 auto;padding:0 18px}header{background:var(--green-dark);color:#fff;padding:24px 0}h1{margin:0 0 8px;font-size:28px}h2{margin:0 0 12px;font-size:20px}h3{margin:18px 0 10px}p{margin:6px 0;color:var(--muted);overflow-wrap:anywhere}header p{color:#dce9e3}.latest-balls{margin:8px 0 10px}.status{display:flex;flex-wrap:wrap;gap:8px;margin-top:12px;width:100%}.pill{display:inline-flex;max-width:100%;padding:6px 10px;border-radius:999px;background:var(--green-soft);color:var(--green);font-weight:900;overflow-wrap:anywhere;white-space:normal}.status .pill:last-child{border-radius:12px}.grid{min-width:0}.grid>*{min-width:0}main{padding:6px 0 32px}section{padding:18px 0;border-bottom:1px solid var(--line)}.grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}.grid.two{grid-template-columns:repeat(2,minmax(0,1fr))}.card,.pack{border:1px solid var(--line);border-radius:8px;padding:14px;background:var(--paper);overflow-wrap:anywhere}.label,.pack-title{color:var(--muted);font-weight:900;margin-bottom:8px}.value{display:flex;flex-wrap:wrap;gap:4px;font-size:22px;font-weight:1000}.value .ball{margin:0}.ball-line{display:flex;flex-wrap:wrap;gap:8px;align-items:center}.ball{width:42px;height:42px;margin:3px;border-radius:50%;display:inline-flex;align-items:center;justify-content:center;font-size:16px;font-weight:1000;background:var(--green);color:#fff}.ball.sub{border:2px solid #aadbc3;color:var(--green);background:#fff}.ball.gold,.actual{background:var(--gold);color:#fff}.ball.specialball,.special{background:var(--gold);color:#fff}.special{display:inline-flex;padding:8px 10px;margin-left:4px;border-radius:8px;font-weight:900}.ball.avoid{border-radius:6px;background:var(--red-soft);color:var(--red)}.core{background:var(--green-soft);border:1px solid #bfe6d1;border-radius:8px;padding:16px}.core .ball{width:46px;height:46px}.specialpack{background:var(--gold-soft);border-color:#e6c887}.low{background:#fff8f7;border-color:#f0bbb5}.cloud-tools{margin-top:12px;padding:16px;border:1px solid #bfe6d1;border-radius:8px;background:var(--green-soft)}.cloud-actions{display:grid;grid-template-columns:1fr 1fr;gap:10px}.cloud-actions button{min-height:48px;border:0;border-radius:8px;background:var(--green);color:#fff;font-size:16px;font-weight:900;cursor:pointer}.cloud-actions .repair-button{background:var(--red)}.cloud-actions button:disabled{opacity:.55;cursor:wait}.cloud-status{margin-top:10px;padding:9px 12px;border-radius:8px;background:#fff;text-align:center;font-weight:900;overflow-wrap:anywhere}.cloud-status.working{color:#805800;background:#fff2c7}.cloud-status.success{color:#075e3d;background:#d8f3e5}.cloud-status.failure{color:#92151b;background:#ffe0e0}.action-times{display:flex;flex-wrap:wrap;justify-content:center;gap:8px 18px;margin-top:10px;color:var(--muted)}.repair-center{display:none;margin-top:9px;text-align:center;color:var(--red);font-weight:900}.repair-center.show{display:block}.scroll{overflow:auto;max-width:100%}table{width:100%;border-collapse:collapse;background:#fff;border:1px solid var(--line);border-radius:8px;overflow:hidden}th,td{border-bottom:1px solid var(--line);padding:9px 10px;text-align:left}th{background:var(--paper);color:var(--muted)}.band{background:#fbfdfc;margin:0 -12px;padding:18px 12px}.notice,footer{color:var(--muted)}footer{padding:18px 0 28px;background:var(--paper)}ul,ol{margin:8px 0 0;padding-left:22px}a{color:var(--green);font-weight:800}@media(max-width:760px){.grid,.grid.two,.cloud-actions{grid-template-columns:1fr}h1{font-size:24px}.wrap{padding:0 12px}.ball{width:38px;height:38px;margin:2px}.core .ball{width:42px;height:42px}th,td{min-width:90px;font-size:14px}.status{gap:6px}.pill{font-size:13px}.action-times{display:grid;grid-template-columns:1fr;text-align:center}}'''

    css += '''@media(max-width:760px){.status{display:grid;grid-template-columns:1fr 1fr;gap:6px}.status .pill{display:block;min-width:0;font-size:13px;text-align:center;border-radius:12px}}'''

    js = '''const updateButton=document.getElementById('manual-update');
const repairButton=document.getElementById('immediate-repair');
const cloudStatus=document.getElementById('cloud-action-status');
const repairCenter=document.getElementById('cloud-repair-center');
let cloudActionRunning=false;
function setCloudStatus(message,state=''){cloudStatus.textContent=message;cloudStatus.className='cloud-status'+(state?' '+state:'');}
function setCloudBusy(busy){cloudActionRunning=busy;updateButton.disabled=busy;repairButton.disabled=busy;}
function freshUrl(path){return path+(path.includes('?')?'&':'?')+'t='+Date.now();}
function pause(ms){return new Promise(resolve=>setTimeout(resolve,ms));}
function taipeiNow(){const parts=new Intl.DateTimeFormat('zh-TW',{timeZone:'Asia/Taipei',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'}).formatToParts(new Date());const p=Object.fromEntries(parts.map(x=>[x.type,x.value]));return p.year+'-'+p.month+'-'+p.day+' '+p.hour+':'+p.minute+':'+p.second;}
function showStoredTimes(){const manual=localStorage.getItem('tw649-manual-update-time')||'尚未執行';const repair=localStorage.getItem('tw649-repair-time')||'尚未執行';document.querySelectorAll('[data-manual-time]').forEach(x=>x.textContent=manual);document.querySelectorAll('[data-repair-time]').forEach(x=>x.textContent=repair);}
async function fetchJson(path,timeout=10000){const controller=new AbortController();const timer=setTimeout(()=>controller.abort(),timeout);try{const response=await fetch(freshUrl(path),{cache:'no-store',headers:{'Cache-Control':'no-cache'},signal:controller.signal});if(!response.ok)throw new Error(path+' HTTP '+response.status);return await response.json();}finally{clearTimeout(timer);}}
function forceFreshReload(reason){const url=new URL(location.href);url.searchParams.set('cloud_refresh',Date.now());url.searchParams.set('reason',reason);location.replace(url.toString());}
function validateCloudBundle(version,analysis,selfTest,repairStatus){if(!version.hash||!version.latest_period)throw new Error('版本資料不完整');if(String(analysis?.latest_draw?.period)!==String(version.latest_period))throw new Error('期別不同步');if(selfTest.passed!==true)throw new Error('全系統自測未通過');if(!['healthy','awaiting_verification'].includes(repairStatus.status))throw new Error('雲端修復狀態異常');return version;}
async function manualUpdateLatest(){if(cloudActionRunning)return;setCloudBusy(true);repairCenter.classList.remove('show');setCloudStatus('正在跳過快取取得最新戰報…','working');try{const [version,analysis]=await Promise.all([fetchJson('version.json'),fetchJson('latest_analysis.json')]);if(!version.hash||String(analysis?.latest_draw?.period)!==String(version.latest_period))throw new Error('雲端期別核對失敗');localStorage.setItem('tw649-version',version.hash);const completed=taipeiNow();localStorage.setItem('tw649-manual-update-time',completed);showStoredTimes();setCloudStatus('更新完成：'+completed+'／第 '+version.latest_period+' 期，正在重新載入。','success');await pause(900);forceFreshReload('manual');}catch(error){setCloudStatus('手動更新失敗：'+error.message+'。請按「當機立即修復」。','failure');setCloudBusy(false);}}
async function clearBrokenClientState(){if('serviceWorker'in navigator){const registrations=await navigator.serviceWorker.getRegistrations();await Promise.all(registrations.map(registration=>registration.unregister()));}if('caches'in window){const keys=await caches.keys();await Promise.all(keys.map(key=>caches.delete(key)));}localStorage.removeItem('tw649-version');}
async function immediateRepair(){if(cloudActionRunning)return;setCloudBusy(true);repairCenter.classList.remove('show');setCloudStatus('正在清除失效快取與背景程式…','working');let lastError=new Error('未知錯誤');try{await clearBrokenClientState();}catch(error){lastError=error;}for(let attempt=1;attempt<=3;attempt++){setCloudStatus('立即修復第 '+attempt+'/3 輪：核對雲端資料…','working');try{const [version,analysis,selfTest,repairStatus]=await Promise.all([fetchJson('version.json'),fetchJson('latest_analysis.json'),fetchJson('self_test_report.json'),fetchJson('self_repair_status.json')]);validateCloudBundle(version,analysis,selfTest,repairStatus);localStorage.setItem('tw649-version',version.hash);const completed=taipeiNow();localStorage.setItem('tw649-repair-time',completed);showStoredTimes();setCloudStatus('修復成功：'+completed+'／第 '+version.latest_period+' 期資料完整，正在重新載入。','success');await pause(1000);forceFreshReload('repair');return;}catch(error){lastError=error;if(attempt<3)await pause(attempt*1200);}}setCloudStatus('三輪修復未通過：'+lastError.message+'。雲端看門狗仍會持續自動修復。','failure');repairCenter.classList.add('show');setCloudBusy(false);}
updateButton.addEventListener('click',manualUpdateLatest);repairButton.addEventListener('click',immediateRepair);
async function refreshVersion(){try{const version=await fetchJson('version.json');const old=localStorage.getItem('tw649-version');if(old&&old!==version.hash){localStorage.setItem('tw649-version',version.hash);forceFreshReload('automatic');}else localStorage.setItem('tw649-version',version.hash);}catch(error){setCloudStatus('雲端版本檢查暫時失敗，可使用立即修復。','failure');}}
showStoredTimes();refreshVersion();setInterval(refreshVersion,60000);document.addEventListener('visibilitychange',()=>{if(!document.hidden)refreshVersion()});window.addEventListener('pageshow',()=>{showStoredTimes();refreshVersion()});if('serviceWorker'in navigator)navigator.serviceWorker.register('service-worker.js').then(registration=>registration.update()).catch(()=>{});'''

    analysis_text = json.dumps(a, ensure_ascii=False, indent=2)
    version = {
        "updated_at": rendered_iso,
        "latest_period": a["latest_draw"]["period"],
        "hash": hashlib.sha256(analysis_text.encode()).hexdigest()[:16],
    }
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
        "theme_color": "#10231e",
        "background_color": "#ffffff",
    }, ensure_ascii=False)
    service_worker = "const C='tw649-top9-v9';self.addEventListener('install',e=>{self.skipWaiting();e.waitUntil(caches.open(C).then(c=>c.addAll(['./','index.html','style.css','app.js'])))});self.addEventListener('activate',e=>e.waitUntil(Promise.all([self.clients.claim(),caches.keys().then(xs=>Promise.all(xs.filter(x=>x!==C).map(x=>caches.delete(x))))])));self.addEventListener('fetch',e=>e.respondWith(fetch(e.request,{cache:'no-store'}).catch(()=>caches.match(e.request))))"

    for base in DESTINATIONS:
        (base / "index.html").write_text(html_text, encoding="utf-8")
        (base / "latest_battle_report.html").write_text(html_text, encoding="utf-8")
        (base / "latest_analysis.json").write_text(analysis_text, encoding="utf-8")
        (base / "prediction_history.json").write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
        (base / "version.json").write_text(json.dumps(version, ensure_ascii=False, indent=2), encoding="utf-8")
        (base / "self_repair_status.json").write_text(repair_status, encoding="utf-8")
        (base / "style.css").write_text(css, encoding="utf-8")
        (base / "app.js").write_text(js, encoding="utf-8")
        (base / "manifest.webmanifest").write_text(manifest, encoding="utf-8")
        (base / "service-worker.js").write_text(service_worker, encoding="utf-8")
