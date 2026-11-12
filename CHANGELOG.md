# Changelog

## 0.1.3rc1

- 成功讀回確認 FAN_ONLY（MODE_STATE=2）時，Fast Poll 與控制後讀回略過 Target；保留 raw cache，Climate target temperature 顯示 None。Fast 失敗及其他模式仍正常嘗試讀取並記錄真正錯誤。
- 搜尋沿用 Scanner callback；先通知原生 Flow 重新載入描述，再更新百分比，避免 refresh 清空進度。首次／Options 搜尋皆顯示 Slave、掃描數與找到設備數；取消時清理 Task。
- Monitor 新增可展開的原始 HEX 手動指令、CRC16 預覽與寫入確認。後端限制 FC03／FC06／FC10，拒絕 Broadcast、未知／保留／重置寫入，控制值與設備能力沿用 Hitachi profile 驗證。
- 所有已登入 HA 使用者可開啟 Monitor 並執行手動指令；清除紀錄與暫停輪詢保留管理員限制。未登入請求由 HA WebSocket 認證拒絕。
- 手動交易使用原 Socket、Worker 與獨占 gate，單次隔離並標示 MANUAL；取消 UI 等待不提前釋放在途交易，寫入不重試，不直接更新 Climate confirmed state。
- 新增預設啟用的室內溫度 Sensor，直接使用 Fast Poll 的 0x0043 confirmed state，不增加 RS485 讀取。
- 保留 75 ms transaction turnaround、1 秒 timeout、Scanner 識別／Capability 規則、背景排程、重連與 Backoff，以及既有品牌圖示與使用者輪詢設定。

## 0.1.2rc2

- 正常合併已驗證的 Fast + Target Poll、Monitor 負載、RTU 接收、Scanner／Identification 修正到 main。
- 修復既有 Ruff 格式問題並保留完整品牌圖示；使用 HACS Release 選單安裝。
