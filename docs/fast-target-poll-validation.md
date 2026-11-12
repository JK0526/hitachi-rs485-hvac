# Fast Poll + Target 驗證紀錄

日期：2026-11-12（Asia/Taipei）

## 基準與交付

- Repository：JK0526/hitachi-rs485-hvac
- 基準 Tag：v0.1.1rc2
- 基準 Commit：70ebad0f3d18707ebeb0585cdf4847430b8a8e93
- 原工作分支：test/identification-nk-0.1.1rc1
- 開發／交付分支：feature/fast-target-poll
- 測試版本：0.1.2rc1（只更新 metadata 與 Monitor 靜態資源版本；未發布 Release）
- 開始時 origin/main 為 26fc477，未包含 rc2 保護，未採用舊 main。
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

## 自動化結果

- Python 3.14.7：完整 pytest **218 passed**，66.42 秒。
- 含 branch coverage：**97.82%**，高於專案 95% 門檻。
- Node 22.23.4：完整 Monitor 前端 **35 passed / 0 failed**。
- Ruff 0.17.0：`ruff check custom_components tests` 通過。
- 本次修改／新增 Python 檔案的 `ruff format --check` 通過。
- `git diff --check` 通過。
- 未發生 sandbox socket 權限限制，實際 localhost socket 測試已執行。

新增測試涵蓋單／多 Slave 依序 Fast + Target、Slow 移除 Target、Fast=0、
Target 實際 timeout／CRC 保留 fast 狀態、控制指令插隊且共用連線、
Scanner 暫停排除背景讀取、超時不重疊不堆積且立即依目標週期繼續、
部分暫停輪次不納入平均、20 輪滾動窗口、80%／100% 邊界與停用等狀態。
既有 Slow=0、75 ms turnaround、接收處理、寫入／讀回、斷線恢復、
identification 與 HA 測試全部通過。

## 未通過的既存環境／格式檢查

- 系統預設 Node 26.9.0 無法啟動，缺少 libsimdjson.33.dylib；
  使用已安裝且可正常執行的 Node 22.23.4 完成前端驗證，未修改系統安裝。
- `ruff check .` 掃到未追蹤 diagnostics/2026-10-28-timeout/supplement.py，
  有 15 個既存 F821。這些使用者診斷檔未修改、未提交。
- `ruff format --check custom_components tests` 仍在 scanner.py、transport.py 失敗。
  已將 git show 70ebad0 的兩檔交由相同 Ruff 檢查，基準也各自 exit 1。
  依本次不改 scanner／Transport 的範圍限制保留，不能宣稱全倉格式檢查成功。
  原 CI 的全倉 format step 預期仍會被這兩個既存格式問題阻擋。

## 現場安裝

參閱 README 的 HACS `update.install` 分支安裝範例，選擇此整合真正的
HACS update entity，指定 `feature/fast-target-poll`，完成後重啟 HA 並確認 0.1.2rc1。
支援公開分支的依據：https://www.hacs.dev/docs/use/entities/update/

已具備 HA 安裝與實機驗證條件，但本次未連線現場設備；
實際 Slave 數量下的輪詢時間、閘道器延遲與實機更新仍待現場驗證。
