# 台灣大樂透每日完整性稽核

- 狀態：通過
- 產生時間：2026-10-06T15:06:59+08:00
- 最新期別：115000093／2026-10-02

- 通過｜official_rows_valid｜valid=2175/2175
- 通過｜no_duplicate_periods｜rows=2175 unique=2175
- 通過｜no_duplicate_dates｜rows=2175 unique=2175
- 通過｜periods_strictly_increasing｜first=96000001 latest=115000093
- 通過｜dates_strictly_increasing｜first=2007-01-02 latest=2026-10-02
- 通過｜draw_dates_calendar_valid｜parsed=2175 future=[]
- 通過｜official_special_schedule_preserved｜official holiday/special draws=113
- 通過｜latest_draw_fresh｜actual=2026-10-02 expected>=2026-10-02
- 通過｜analysis_matches_official_csv｜{'csv_period': 115000093, 'analysis_period': 115000093}
- 通過｜analysis_row_count_matches｜csv=2175 engine=2175
- 通過｜prediction_history_no_duplicate_revisions｜[]
- 通過｜prediction_history_no_stale_pending｜[]
- 通過｜prediction_settlements_match_official｜[]
- 通過｜all_report_artifacts_exist｜[]
- 通過｜five_destinations_byte_identical｜[]