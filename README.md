# 台灣大樂透鐵律預測系統

本版依照 539 戰報鐵律規格改為台灣大樂透專用：

- 先建立官方全歷史 SQLite 與 CSV 資料庫，再產生候選號碼。
- 大樂透採 6/49 + 特別號，資料表分開保存本號與特別號。
- 多窗口分析近 5、10、20、50、100 期與長期資料。
- 強牌分層：最強單支、2中1、3中1、5中2、9中3，另列特別號單支與 3 碼觀察。
- 每期會結算 Top6、Top12、Top18、建議組合、強牌組、特別號命中。
- v3 模式加入 520 期 + 180 期雙回測校準，並追加最終權重 520 期回測；負邊際模型會降權或隔離。
- v4 模式加入失手回饋：Top6低命中或特別號Top3失手會直接降權，不等下一次爆掉。
- v5 模式加入每日雲端全系統掃描：每天自動更新、檢測，失敗會改跑全量重建修復並同步手機版。
- v6 模式把大樂透戰報對齊 539 規格：核心決策、逐號驗算、短包強牌、低機率避險、每日更新鐵律、模型滾動調整完整輸出。
- v7 模式新增電腦版、手機版、GitHub Pages逐檔同步檢測；不同步或未更新會直接判定失敗。
- 升級版保留 Bayesian/Dirichlet 平滑、EWMA 快慢週期、Markov 轉移、gap hazard、卡方區間/尾數平衡與組合搜尋。
- 每次輸出前會跑自我檢測，檢測失敗就中止。
- 輸出本機戰報與 `mobile_cloud` 雲端手機獨立版。

## 一鍵更新

```powershell
python .\lotto649_ironlaw_system.py --all
```

離線重算既有資料庫：

```powershell
python .\lotto649_ironlaw_system.py --analyze-only
```

完成後會產生：

- `data/lotto649.sqlite`
- `data/lotto649.csv`
- `reports/latest_battle_report.html`
- `reports/latest_analysis.json`
- `reports/self_test_report.json`
- `mobile_cloud/index.html`
- `docs/index.html`

## 雲端手機獨立版

把本資料夾放到 GitHub repo 後，啟用 GitHub Pages 與 Actions，Pages 發布來源設為 `main` 分支的 `/docs`。`.github/workflows/update-mobile-cloud.yml` 會每天台灣時間 08:30 做全系統掃描；週二、週五開獎後 22:20 與 23:10 追加更新。流程會驗證 `self_test_report.json`，失敗會自動改跑全量重建修復，通過後才提交 `data`、`reports`、`mobile_cloud` 與 `docs`。手機只需要打開 GitHub Pages 網址，不需要透過家裡電腦。

## 重要提醒

本系統是歷史統計與回測研究，不保證開獎命中或獲利，請量力而為。
