# 台灣大樂透每日完整性稽核

- 狀態：通過
- 產生時間：2026-10-09T02:07:20+08:00
- 最新期別：115000094／2026-10-06

- 通過｜official_rows_valid｜valid=2176/2176
- 通過｜no_duplicate_periods｜rows=2176 unique=2176
- 通過｜no_duplicate_dates｜rows=2176 unique=2176
- 通過｜periods_strictly_increasing｜first=96000001 latest=115000094
- 通過｜dates_strictly_increasing｜first=2007-01-02 latest=2026-10-06
- 通過｜draw_dates_calendar_valid｜parsed=2176 future=[]
- 通過｜official_special_schedule_preserved｜official holiday/special draws=113
- 通過｜latest_draw_fresh｜actual=2026-10-06 expected>=2026-10-06
- 通過｜analysis_matches_official_csv｜{'csv_period': 115000094, 'analysis_period': 115000094}
- 通過｜analysis_row_count_matches｜csv=2176 engine=2176
- 通過｜prediction_history_no_duplicate_revisions｜[]
- 通過｜prediction_history_no_stale_pending｜[]
- 通過｜prediction_settlements_match_official｜[]
- 通過｜all_report_artifacts_exist｜[]
- 通過｜five_destinations_byte_identical｜[]