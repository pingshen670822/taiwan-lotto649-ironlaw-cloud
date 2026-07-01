# 台灣大樂透鐵律預測系統

本版依照 539 戰報鐵律規格改為台灣大樂透專用：

- 先建立官方全歷史 SQLite 與 CSV 資料庫，再產生候選號碼。
- 大樂透採 6/49 + 特別號，資料表分開保存本號與特別號。
- 多窗口分析近 5、10、20、50、100 期與長期資料。
- 強牌分層：最強單支、2中1、3中1、5中2、9中3，另列特別號單支與 3 碼觀察。
- 每期會結算 Top6、Top12、Top18、建議組合、強牌組、特別號命中。
- 輸出本機戰報與 `mobile_cloud` 雲端手機獨立版。

## 一鍵更新

```powershell
python .\lotto649_ironlaw_system.py --all
```

完成後會產生：

- `data/lotto649.sqlite`
- `data/lotto649.csv`
- `reports/latest_battle_report.html`
- `reports/latest_analysis.json`
- `mobile_cloud/index.html`

## 雲端手機獨立版

把本資料夾放到 GitHub repo 後，啟用 GitHub Pages 與 Actions，`.github/workflows/update-mobile-cloud.yml` 會在台灣時間週二、週五晚間開獎後自動更新並部署 `mobile_cloud`。手機只需要打開 GitHub Pages 網址，不需要透過家裡電腦。

## 重要提醒

本系統是歷史統計與回測研究，不保證開獎命中或獲利，請量力而為。
