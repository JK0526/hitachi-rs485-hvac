# Fast Poll + Target 驗證紀錄

日期：2026-11-12（Asia/Taipei）

## 基準與交付

- Repository：JK0526/hitachi-rs485-hvac
- 基準 Tag：v0.1.1rc2
- 基準 Commit：70ebad0f3d18707ebeb0585cdf4847430b8a8e93
- 原工作分支：test/identification-nk-0.1.1rc1
- 整合來源：feature/fast-target-poll（本文件記錄整合前驗證）
- 測試版本：0.1.2rc2（已發布 Pre-release v0.1.2rc1，合併後版本為 rc2）
- 整合前 origin/main 為 26fc477；最終以正常合併保留雙方歷史與設定。
- 未追蹤的 cat_hvac.png、diagnostics/ 保留原狀，不納入 commit。

## 最終輪詢與負載行為

- Fast 預設 2 秒；每台 FC03 0040 count 5 → 0023 count 1，再進入下一台。
- Slow 預設 30 秒，只讀取已支援 Optional Registers。
- 原有單一 target-period scheduler 與 round lock 保留；超時完成當輪，
  下一輪依起始時間加週期判定，不額外固定等待，不重疊或堆積。
- 兩筆皆走原 Transport poll queue；控制優先、Scanner 獨占、暫停、
  Fast=0／Slow=0、Backoff 與重連機制保留。
- Target timeout／CRC／protocol error 使用原有 optional-read 路徑，
  不增加 fast health failures，也不清除已確認 raw 值。
  Climate 原有 target unavailable 呈現方式保持不變；其他正常狀態不失效。
  GatewayError 仍走既有共享閘道器離線處理。
- 只有所有 Slave 的 Fast + Target 都完成嘗試的輪次才加入最近 20 輪 deque。
  耗時包括 queue 等待、turnaround、兩筆讀取與錯誤處理。
- Monitor 負載 = 平均 ms / (設定秒數 × 1000) × 100。
  滿 20 輪才套用 <80%、80%～<100%、>=100% 警告。
  樣本不足可顯示暫時負載，但不判定警告；停用、無設備、無樣本、執行時暫停不計算百分比。
- 僅 Monitor 顯示指定繁體中文警告，不發 HA Notification 或持續性警告 Log，
  不改變輪詢／通訊行為。既有通訊統計分開呈現。
- transport.py、scanner.py、profiles.py、models.py、climate.py 與基準內容完全相同；
  75 ms turnaround、1 秒預設 timeout、單一 worker、一筆在途交易、寫入不重試全部保留。

## 整合與 CI 修復

本項功能已由 `feature/fast-target-poll` 正常合併至 `main`；合併保留雙親歷史，
原有 `hacs.json` 設定、所有先前 Tags 與 Releases 均保留。版本統一更新為
`0.1.2rc2`。送風模式 Target Exception 03 不在本版本修改範圍。

上一版 CI 的 lint 通過，format 因 `scanner.py`、`transport.py` 排版失敗。
兩檔只執行 Ruff 自動格式整理，前後 Python AST 相同，未改變程式行為。

整合版本機驗證結果將隨 `v0.1.2rc2` 發布前補記；GitHub Actions 必須
全數通過後才建立該 Tag 與 Pre-release。

品牌檔案 `icon.png`、`icon@2x.png` 均直接沿用 main 已提交圖示；Metadata
測試同時檢查 PNG signature 與 256×256／512×512 尺寸。

## 現場安裝

HACS 資訊重新整理後，可在 Beta 版本選單安裝 v0.1.2rc2 Pre-release。
安裝後重新啟動 HA。本次尚未連線現場設備；實際 Slave 數量下的輪詢時間、
閘道器延遲與實機更新仍待現場驗證。
