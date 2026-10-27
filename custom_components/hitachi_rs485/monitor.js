// Read-only diagnostic view. Every detail comes from the existing runtime snapshot.
const FUNCTION_NAMES = {
  1: "Read Coils",
  2: "Read Discrete Inputs",
  3: "Read Holding Registers",
  4: "Read Input Registers",
  5: "Write Single Coil",
  6: "Write Single Register",
  16: "Write Multiple Registers",
};
const EXCEPTION_MEANINGS = {
  1: "Illegal Function",
  2: "Illegal Data Address",
  3: "Illegal Data Value",
  4: "Slave Device Failure / Communication Failure",
  6: "Indoor Unit Connection In Progress",
};
const LOCAL_HISTORY_LIMIT = 1000;

class HitachiRS485Monitor extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._generation = 0;
    this._paused = false;
    this._autoScroll = true;
    this._entry = null;
    this._latest = null;
    this._history = [];
    this._transactions = [];
    this._selectedTransactionKey = null;
    this._unsubscribe = null;
    this._ready = false;
  }

  set hass(value) {
    this._hass = value;
    if (this.isConnected && this._connection !== value.connection) this._connect();
  }

  connectedCallback() {
    if (!this._ready) {
      this.shadowRoot.innerHTML = `<style>
        :host{display:flex;flex-direction:column;height:100vh;height:100dvh;overflow:hidden;padding:16px;color:var(--primary-text-color);background:var(--primary-background-color);box-sizing:border-box}
        *{box-sizing:border-box} h1{font-size:22px;margin:0} h2{font-size:16px;margin:0 0 12px} h3{font-size:14px;margin:0 0 8px}
        header{display:flex;align-items:center;gap:12px;flex:0 0 auto;margin-bottom:10px}.toolbar{display:flex;align-items:center;gap:8px;flex-wrap:wrap;flex:0 0 auto;margin-bottom:9px}
        button,select{font:inherit;min-height:36px;padding:7px 9px;border:1px solid var(--divider-color);border-radius:6px;color:var(--primary-text-color);background:var(--card-background-color)}
        button{cursor:pointer}.toolbar label{display:flex;align-items:center;gap:5px}.toolbar input{accent-color:var(--primary-color)}
        #error{color:var(--error-color);margin:0 0 8px;flex:0 0 auto}
        .layout{display:grid;grid-template-columns:minmax(0,1fr) minmax(320px,380px);grid-template-areas:"trace sidebar";gap:14px;flex:1;min-height:0;overflow:hidden}
        .card{min-width:0;padding:12px;border:1px solid var(--divider-color);border-radius:9px;background:var(--card-background-color)}
        .trace-panel{grid-area:trace;display:flex;flex-direction:column;min-height:0}.sidebar{grid-area:sidebar;display:grid;align-content:start;gap:10px;min-width:0;height:100%;min-height:0;overflow-y:auto;padding-right:2px}
        .trace-list{flex:1;min-height:0;overflow:auto;min-width:0}.trace-entry{display:grid;grid-template-columns:88px 78px minmax(0,1fr) 64px;gap:8px;align-items:start;padding:4px 6px;border:1px solid transparent;border-radius:5px;line-height:1.45;font:12px/1.45 var(--code-font-family,monospace);cursor:pointer}
        .trace-entry:hover{background:var(--secondary-background-color)}.trace-entry.selected{border-color:var(--primary-color);background:var(--secondary-background-color)}
        .trace-time,.trace-latency{color:var(--secondary-text-color);white-space:nowrap}.trace-tx,.trace-raw-tx{color:#42a5f5}.trace-rx,.trace-ok,.trace-raw-rx{color:#66bb6a}.trace-timeout{color:#ffb74d}.trace-crc-error,.trace-error{color:#ef5350}.trace-event,.trace-raw-event{color:#ba68c8}
        .trace-raw{display:block;min-width:0;overflow-x:auto;white-space:pre}.trace-status{white-space:pre}.trace-latency{text-align:right}
        .metric-grid{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:5px 10px;margin:0;font-size:13px}.metric-grid dt{color:var(--secondary-text-color)}.metric-grid dd{margin:0;text-align:right;font-variant-numeric:tabular-nums}
        .stats-grid{display:grid;grid-template-columns:1fr 1fr;gap:2px 12px}.stat{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:4px 2px;border-bottom:1px solid var(--divider-color)}.stat span{color:var(--secondary-text-color);font-size:12px}.stat strong{font-size:14px;font-variant-numeric:tabular-nums}
        .section-caption{margin:-6px 0 8px;color:var(--secondary-text-color);font-size:11px}
        .device-list{display:grid;gap:4px;max-height:240px;overflow-y:auto}.device-row{width:100%;display:flex;align-items:center;justify-content:space-between;gap:8px;text-align:left;border:0;padding:6px 7px}.device-row.active{outline:1px solid var(--primary-color)}.device-main{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.device-health{white-space:nowrap;color:var(--secondary-text-color);font-size:12px}.online{color:var(--success-color,var(--primary-text-color))}.offline{color:var(--error-color)}
        .decoder-card{position:static}.decoder-grid{display:grid;grid-template-columns:minmax(100px,auto) minmax(0,1fr);gap:5px 9px;margin:0;max-height:min(38vh,360px);overflow-y:auto;font-size:13px}.decoder-grid dt{color:var(--secondary-text-color)}.decoder-grid dd{margin:0;overflow-wrap:anywhere}.empty{color:var(--secondary-text-color);padding:10px 4px}
        @media(max-width:900px){:host{height:auto;min-height:100vh;min-height:100dvh;overflow:visible}.layout{grid-template-columns:minmax(0,1fr);grid-template-areas:"sidebar" "trace";flex:none;min-height:auto;overflow:visible}.sidebar{height:auto;overflow:visible}.trace-list{flex:none;min-height:120px;max-height:55vh}.decoder-grid{max-height:40vh}}
        @media(max-width:560px){:host{padding:12px}.trace-entry{grid-template-columns:70px 65px minmax(0,1fr);gap:5px;font-size:11px}.trace-latency{grid-column:3;text-align:left}.stats-grid{grid-template-columns:1fr 1fr}}
      </style>
      <header><h1>Hitachi RS485 Monitor</h1></header>
      <div class="toolbar">
        <select id="gateway" aria-label="選擇閘道器"></select>
        <select id="slave-filter" aria-label="Slave 篩選"><option value="all">Slave：全部</option></select>
        <select id="type-filter" aria-label="方向與類型篩選">
          <option value="all">類型：全部</option><option value="TX">TX</option><option value="RX">RX</option><option value="OK">OK</option><option value="EVENT">事件</option><option value="Error">錯誤</option>
        </select>
        <button id="clear">清除封包</button><button id="pause">暫停畫面</button>
        <button id="poll-control" disabled>輪詢狀態載入中</button>
        <label><input id="auto-scroll" type="checkbox" checked>自動捲動</label>
      </div>
      <div id="error" role="alert"></div>
      <div class="layout">
        <section class="card trace-panel"><h2>原始封包</h2><div id="trace" class="trace-list" aria-label="Modbus Raw HEX"></div></section>
        <aside id="sidebar" class="sidebar">
          <section class="card"><h2>閘道器</h2><dl class="metric-grid">
            <dt>狀態</dt><dd id="gateway-status" aria-live="polite">等待資料</dd><dt>設定週期</dt><dd id="gateway-poll">—</dd>
            <dt>背景輪詢</dt><dd id="gateway-poll-state">等待資料</dd>
            <dt>設備</dt><dd id="gateway-devices">—</dd><dt>最近輪詢</dt><dd id="gateway-last">—</dd>
            <dt>平均輪詢</dt><dd id="gateway-average">—</dd><dt>佇列</dt><dd id="gateway-queue">—</dd>
            <dt>佇列峰值</dt><dd id="gateway-queue-peak">—</dd>
          </dl></section>
          <section id="decoder-card" class="card decoder-card"><h2>封包解析</h2><dl id="decoder" class="decoder-grid"><dd class="empty">選取一筆封包以檢視內容。</dd></dl></section>
          <section class="card"><h2>設備狀態 <small id="device-summary">0 / 0 在線</small></h2><div id="devices" class="device-list"></div></section>
          <section class="card"><h2>通訊統計</h2><div id="stats-caption" class="section-caption">自本次啟動起累計</div><div id="stats-grid" class="stats-grid">
            <div class="stat"><span>成功</span><strong id="stat-success">0</strong></div><div class="stat"><span>失敗</span><strong id="stat-failed">0</strong></div>
            <div class="stat"><span>逾時</span><strong id="stat-timeout">0</strong></div><div class="stat"><span>CRC 錯誤</span><strong id="stat-crc">0</strong></div>
            <div class="stat"><span>設備例外</span><strong id="stat-exception">0</strong></div>
            <div class="stat"><span>重新連線</span><strong id="stat-reconnect">0</strong></div>
          </div></section>
        </aside>
      </div>`;
      this.shadowRoot.querySelector("#gateway").onchange = (event) => this._select(event.target.value);
      this.shadowRoot.querySelector("#slave-filter").onchange = () => this._filterChanged();
      this.shadowRoot.querySelector("#type-filter").onchange = () => this._filterChanged();
      this.shadowRoot.querySelector("#clear").onclick = () => this._clear();
      this.shadowRoot.querySelector("#poll-control").onclick = () => this._togglePolling();
      this.shadowRoot.querySelector("#pause").textContent = "暫停畫面";
      this.shadowRoot.querySelector("#pause").onclick = () => {
        this._paused = !this._paused;
        this.shadowRoot.querySelector("#pause").textContent = this._paused ? "繼續畫面" : "暫停畫面";
        if (!this._paused) this._render();
      };
      this.shadowRoot.querySelector("#auto-scroll").onchange = (event) => {
        const enabled = Boolean(event.target.checked);
        const changed = enabled !== this._autoScroll;
        this._autoScroll = enabled;
        if (changed && enabled) this._renderTrace({ preserveAnchor: false });
      };
      this._ready = true;
    }
    if (this._hass) this._connect();
  }

  disconnectedCallback() {
    this._generation++;
    this._stopSubscription();
    this._connection = null;
  }

  _stopSubscription() {
    if (this._unsubscribe) {
      this._unsubscribe().catch(() => {});
      this._unsubscribe = null;
    }
  }

  _error(error) {
    this.shadowRoot.querySelector("#error").textContent = String(error.message || error);
  }

  async _connect() {
    this._connection = this._hass.connection;
    this._stopSubscription();
    const generation = ++this._generation;
    try {
      const gateways = await this._hass.callWS({ type: "hitachi_rs485/monitor/list" });
      if (generation !== this._generation) return;
      const select = this.shadowRoot.querySelector("#gateway");
      select.replaceChildren();
      for (const gateway of gateways) {
        const option = document.createElement("option");
        option.value = gateway.entry_id;
        option.textContent = gateway.title;
        select.append(option);
      }
      select.disabled = gateways.length === 0;
      if (gateways.length) {
        select.value = gateways.some((item) => item.entry_id === this._entry) ? this._entry : gateways[0].entry_id;
        await this._select(select.value);
      } else {
        this._entry = null;
        this._latest = null;
        this._renderPollControl(null);
        this._resetHistory();
        this._transactions = [];
        this.shadowRoot.querySelector("#gateway-status").textContent = "尚未載入";
        this.shadowRoot.querySelector("#trace").replaceChildren();
        this.shadowRoot.querySelector("#devices").replaceChildren();
        this.shadowRoot.querySelector("#device-summary").textContent = "0 / 0 在線";
        this._renderDecoder(null);
      }
    } catch (error) { if (generation === this._generation) this._error(error); }
  }

  async _select(entry) {
    this._stopSubscription();
    const changedGateway = this._entry !== entry;
    this._entry = entry;
    if (changedGateway) {
      this._latest = null;
      this._pollControlPending = false;
      this._renderPollControl(null);
      this._resetHistory();
      this._transactions = [];
      this._selectedTransactionKey = null;
      this.shadowRoot.querySelector("#slave-filter").value = "all";
      this.shadowRoot.querySelector("#type-filter").value = "all";
      this.shadowRoot.querySelector("#gateway-status").textContent = "等待資料";
      this.shadowRoot.querySelector("#trace").replaceChildren();
      this._renderDecoder(null);
    }
    const generation = ++this._generation;
    try {
      const unsubscribe = await this._connection.subscribeMessage((snapshot) => {
        if (generation !== this._generation) return;
        this._latest = snapshot;
        this._ingestSnapshot(snapshot.entries || []);
        this._renderPollControl(snapshot);
        if (!this._paused) this._render();
      }, { type: "hitachi_rs485/monitor/subscribe", entry_id: entry });
      if (generation !== this._generation) { await unsubscribe(); return; }
      this._unsubscribe = unsubscribe;
      this.shadowRoot.querySelector("#error").textContent = "";
    } catch (error) { if (generation === this._generation) this._error(error); }
  }

  _resetHistory() {
    this._history = [];
    this._transactions = [];
    this._selectedTransactionKey = null;
  }

  _ingestSnapshot(entries) {
    this._history = HitachiRS485Monitor.mergeTraceHistory(
      this._history,
      entries,
      LOCAL_HISTORY_LIMIT,
    );
    this._transactions = HitachiRS485Monitor.buildTransactions(this._history);
    if (
      this._selectedTransactionKey &&
      !this._transactions.some((transaction) => transaction.key === this._selectedTransactionKey)
    ) {
      this._selectedTransactionKey = null;
    }
  }

  async _clear() {
    if (!this._entry) return;
    const generation = this._generation;
    try {
      await this._hass.callWS({ type: "hitachi_rs485/monitor/clear", entry_id: this._entry });
      if (generation !== this._generation) return;
      this._latest = this._latest ? { ...this._latest, entries: [] } : null;
      this._resetHistory();
      this._render();
      this.shadowRoot.querySelector("#error").textContent = "";
    } catch (error) { if (generation === this._generation) this._error(error); }
  }

  async _togglePolling() {
    const snapshot = this._latest;
    const entry = this._entry;
    const generation = this._generation;
    if (!snapshot || !entry || this._pollControlPending || Number(snapshot.poll_interval) === 0) return;
    const paused = !snapshot.runtime_poll_paused;
    this._pollControlPending = true;
    this._renderPollControl(snapshot);
    try {
      const result = await this._hass.callWS({
        type: "hitachi_rs485/monitor/poll",
        entry_id: entry,
        paused,
      });
      if (generation !== this._generation || entry !== this._entry) return;
      this._latest = { ...this._latest, ...result };
      this.shadowRoot.querySelector("#error").textContent = "";
    } catch (error) {
      if (generation === this._generation && entry === this._entry) this._error(error);
    } finally {
      if (generation === this._generation && entry === this._entry) {
        this._pollControlPending = false;
        this._renderPollControl(this._latest);
      }
    }
  }

  _renderPollControl(snapshot) {
    const button = this.shadowRoot.querySelector("#poll-control");
    const state = this.shadowRoot.querySelector("#gateway-poll-state");
    if (!snapshot || snapshot.poll_interval == null) {
      button.textContent = "輪詢狀態載入中";
      button.disabled = true;
      state.textContent = "尚未載入";
      return;
    }
    if (Number(snapshot.poll_interval) === 0) {
      button.textContent = "輪詢已由設定停用";
      button.disabled = true;
      state.textContent = "設定停用";
      return;
    }
    const paused = Boolean(snapshot.runtime_poll_paused);
    button.textContent = paused ? "恢復輪詢" : "暫停輪詢";
    button.disabled = Boolean(this._pollControlPending);
    state.textContent = paused ? "已暫停" : "執行中";
  }

  _render() {
    if (!this._latest) return;
    const sidebar = this.shadowRoot.querySelector("#sidebar");
    const sidebarScrollTop = sidebar.scrollTop;
    const snapshot = this._latest;
    const coordinator = snapshot.coordinator || {};
    const stats = snapshot.statistics || {};
    this.shadowRoot.querySelector("#gateway-status").textContent = snapshot.connected ? "● 已連線" : "○ 已中斷";
    this.shadowRoot.querySelector("#gateway-poll").textContent = Number(snapshot.poll_interval) === 0 ? "設定停用" : `${Number(snapshot.poll_interval ?? 0).toFixed(1)} 秒`;
    this._renderPollControl(snapshot);
    this.shadowRoot.querySelector("#gateway-devices").textContent = `${coordinator.available_slaves ?? 0} / ${coordinator.configured_slaves ?? 0} 在線`;
    this.shadowRoot.querySelector("#gateway-last").textContent = `${Number(coordinator.last_poll_round_ms || 0).toFixed(1)} ms`;
    this.shadowRoot.querySelector("#gateway-average").textContent = `${Number(coordinator.average_poll_round_ms || 0).toFixed(1)} ms`;
    this.shadowRoot.querySelector("#gateway-queue").textContent = String(coordinator.queue_depth ?? 0);
    this.shadowRoot.querySelector("#gateway-queue-peak").textContent = String(coordinator.queue_peak ?? 0);
    this.shadowRoot.querySelector("#device-summary").textContent = `${coordinator.available_slaves ?? 0} / ${coordinator.configured_slaves ?? 0} 在線`;
    this.shadowRoot.querySelector("#stat-success").textContent = String(stats.successful_requests ?? 0);
    this.shadowRoot.querySelector("#stat-failed").textContent = String(stats.failed_requests ?? 0);
    this.shadowRoot.querySelector("#stat-timeout").textContent = String(stats.timeout_count ?? 0);
    this.shadowRoot.querySelector("#stat-crc").textContent = String(stats.crc_error_count ?? 0);
    this.shadowRoot.querySelector("#stat-exception").textContent = String(stats.modbus_exception_count ?? 0);
    this.shadowRoot.querySelector("#stat-reconnect").textContent = String(stats.reconnect_count ?? 0);
    this._renderFilters(snapshot.devices || []);
    this._renderDevices(snapshot.devices || []);
    this._renderTrace({ preserveAnchor: true });
    const selected = this._transactions.find((transaction) => transaction.key === this._selectedTransactionKey);
    this._renderDecoder(selected || null, snapshot.devices || []);
    if (sidebar.scrollTop !== sidebarScrollTop) sidebar.scrollTop = sidebarScrollTop;
  }

  _filterChanged() {
    this._renderDevices(this._latest?.devices || []);
    this._renderTrace({ preserveAnchor: false });
  }

  _renderDevices(devices) {
    const list = this.shadowRoot.querySelector("#devices");
    list.replaceChildren();
    if (!devices.length) {
      const empty = document.createElement("div"); empty.className = "empty"; empty.textContent = "尚未設定 Slave"; list.append(empty); return;
    }
    for (const device of devices) {
      const button = document.createElement("button");
      button.className = `device-row${this.shadowRoot.querySelector("#slave-filter").value === String(device.slave) ? " active" : ""}`;
      button.onclick = () => {
        const filter = this.shadowRoot.querySelector("#slave-filter");
        filter.value = filter.value === String(device.slave) ? "all" : String(device.slave);
        this._filterChanged();
      };
      const name = document.createElement("span"); name.className = "device-main";
      name.textContent = `${device.available ? "●" : "○"} #${device.slave} ${String(device.model || "").toUpperCase()}`;
      const health = document.createElement("span"); health.className = `device-health ${device.available ? "online" : "offline"}`;
      health.textContent = device.available ? "在線" : "離線";
      if (device.consecutive_failures > 0) health.textContent += ` · 失敗 ${device.consecutive_failures} 次`;
      button.append(name, health); list.append(button);
    }
  }

  _renderFilters(devices) {
    const filter = this.shadowRoot.querySelector("#slave-filter");
    const selected = filter.value || "all";
    const ids = new Set(devices.map((item) => Number(item.slave)));
    for (const transaction of this._transactions) if (transaction.slave != null) ids.add(transaction.slave);
    filter.replaceChildren();
    const all = document.createElement("option"); all.value = "all"; all.textContent = "Slave：全部"; filter.append(all);
    for (const slave of [...ids].sort((a, b) => a - b)) {
      const option = document.createElement("option"); option.value = String(slave); option.textContent = `Slave ${slave}`; filter.append(option);
    }
    filter.value = ids.has(Number(selected)) ? selected : "all";
  }

  _renderTrace({ preserveAnchor = false } = {}) {
    const list = this.shadowRoot.querySelector("#trace");
    const oldScrollTop = list.scrollTop || 0;
    const anchor = !this._autoScroll && preserveAnchor ? this._captureAnchor(list) : null;
    list.replaceChildren();
    const slaveFilter = this.shadowRoot.querySelector("#slave-filter").value;
    const typeFilter = this.shadowRoot.querySelector("#type-filter").value;
    const devices = this._latest?.devices || [];
    this._transactions.forEach((transaction) => {
      if (slaveFilter !== "all" && transaction.slave !== Number(slaveFilter)) return;
      for (const entry of transaction.entries) {
        const status = String(entry.direction).toUpperCase();
        if (typeFilter === "TX" && status !== "TX") continue;
        if (typeFilter === "RX" && status !== "RX") continue;
        if (typeFilter === "OK" && status !== "OK") continue;
        if (typeFilter === "EVENT" && status !== "EVENT") continue;
        if (typeFilter === "Error" && !["TIMEOUT", "CRC ERROR", "ERROR"].includes(status)) continue;
        const date = new Date(entry.timestamp);
        const time = `${date.toLocaleTimeString(this._hass?.language || "zh-TW", { hour12: false })}.${String(date.getMilliseconds()).padStart(3, "0")}`;
        const classKey = status.toLowerCase().replaceAll(" ", "-");
        const row = document.createElement("div");
        row.className = `trace-entry trace-${classKey}${this._selectedTransactionKey === transaction.key ? " selected" : ""}`;
        row.dataset.entryKey = HitachiRS485Monitor.entryKey(entry);
        row.dataset.entryTimestamp = String(entry.timestamp ?? "");
        row.onclick = () => {
          this._selectedTransactionKey = transaction.key;
          this._renderTrace({ preserveAnchor: true });
          this._renderDecoder(transaction, devices);
          this._scrollDecoderIntoViewIfNeeded();
        };
        const timestamp = document.createElement("span"); timestamp.className = "trace-time"; timestamp.textContent = time;
        const direction = document.createElement("span"); direction.className = `trace-status trace-${classKey}`; direction.textContent = status.padEnd(9);
        const raw = document.createElement("span"); raw.className = `trace-raw${["TX", "RX", "EVENT"].includes(status) ? ` trace-raw-${status.toLowerCase()}` : ""}`; raw.textContent = entry.raw_hex || "";
        row.append(timestamp, direction, raw);
        if (entry.latency_ms != null) { const latency = document.createElement("span"); latency.className = "trace-latency"; latency.textContent = `${Number(entry.latency_ms).toFixed(1)} ms`; row.append(latency); }
        list.append(row);
      }
    });
    if (!list.children.length) { const empty = document.createElement("div"); empty.className = "empty"; empty.textContent = "沒有符合篩選條件的 Trace"; list.append(empty); }
    if (this._autoScroll) list.scrollTop = list.scrollHeight;
    else if (!preserveAnchor) list.scrollTop = 0;
    else this._restoreAnchor(list, anchor, oldScrollTop);
  }

  _captureAnchor(list) {
    const viewport = list.getBoundingClientRect();
    for (const row of list.children) {
      if (!row.className.includes("trace-entry")) continue;
      const rect = row.getBoundingClientRect();
      if (rect.bottom <= viewport.top || rect.top >= viewport.bottom) continue;
      return {
        key: row.dataset.entryKey,
        offset: rect.top - viewport.top,
        timestamp: Date.parse(row.dataset.entryTimestamp),
        fallbackScrollTop: list.scrollTop || 0,
      };
    }
    return { key: null, offset: 0, timestamp: NaN, fallbackScrollTop: list.scrollTop || 0 };
  }

  _restoreAnchor(list, anchor, oldScrollTop) {
    if (!anchor) {
      list.scrollTop = oldScrollTop;
      return;
    }
    const rows = [...list.children].filter((row) => row.dataset?.entryKey);
    let row = rows.find((item) => item.dataset.entryKey === anchor.key);
    if (!row && Number.isFinite(anchor.timestamp)) {
      row = rows
        .map((item) => ({ item, timestamp: Date.parse(item.dataset.entryTimestamp) }))
        .filter((item) => Number.isFinite(item.timestamp) && item.timestamp >= anchor.timestamp)
        .sort((left, right) => left.timestamp - right.timestamp)[0]?.item;
    }
    if (!row) row = rows[0];
    if (!row) {
      list.scrollTop = 0;
      return;
    }
    const rowTop = row.getBoundingClientRect().top;
    const viewportTop = list.getBoundingClientRect().top;
    const desiredOffset = anchor.key ? anchor.offset : 0;
    list.scrollTop = Math.max(0, list.scrollTop + rowTop - viewportTop - desiredOffset);
  }

  _renderDecoder(
    transaction,
    devices = this._latest?.devices || [],
  ) {
    const decoder = this.shadowRoot.querySelector("#decoder");
    decoder.replaceChildren();
    if (!transaction) { const empty = document.createElement("dd"); empty.className = "empty"; empty.textContent = "選取一筆 Trace 以檢視封包。"; decoder.append(empty); return; }
    const device = devices.find((item) => item.slave === transaction.slave) || null;
    for (const [label, value] of HitachiRS485Monitor.decodeTransaction(transaction, device)) {
      const dt = document.createElement("dt"); dt.textContent = label;
      const dd = document.createElement("dd"); dd.textContent = value;
      decoder.append(dt, dd);
    }
  }

  _scrollDecoderIntoViewIfNeeded() {
    const sidebar = this.shadowRoot.querySelector("#sidebar");
    const decoderCard = this.shadowRoot.querySelector("#decoder-card");
    if (!sidebar || !decoderCard || sidebar.scrollHeight <= sidebar.clientHeight) return;
    const sidebarBounds = sidebar.getBoundingClientRect();
    const decoderBounds = decoderCard.getBoundingClientRect();
    if (decoderBounds.top < sidebarBounds.top || decoderBounds.bottom > sidebarBounds.bottom) {
      decoderCard.scrollIntoView?.({ block: "nearest", behavior: "smooth" });
    }
  }

  static parseHex(value) {
    const text = String(value || "").trim();
    if (!text) return [];
    const parts = text.split(/\s+/);
    return parts.every((part) => /^[0-9a-f]{2}$/i.test(part)) ? parts.map((part) => Number.parseInt(part, 16)) : [];
  }

  static buildTransactions(entries) {
    const transactions = [];
    let current = null;
    for (const entry of entries) {
      const direction = String(entry.direction).toUpperCase();
      if (direction === "EVENT") {
        transactions.push({ tx: null, rx: null, statuses: [entry], entries: [entry], slave: null });
        continue;
      }
      if (direction === "TX") {
        current = { tx: entry, rx: null, statuses: [], entries: [entry], slave: this.parseHex(entry.raw_hex)[0] ?? null };
        transactions.push(current);
      } else {
        if (!current) { current = { tx: null, rx: null, statuses: [], entries: [], slave: null }; transactions.push(current); }
        current.entries.push(entry);
        if (String(entry.direction).toUpperCase() === "RX") current.rx = entry;
        else current.statuses.push(entry);
      }
    }
    for (const transaction of transactions) {
      transaction.key = this.entryKey(transaction.tx || transaction.entries[0]);
    }
    return transactions;
  }

  static entryKey(entry) {
    return JSON.stringify([
      entry?.timestamp ?? "",
      String(entry?.direction ?? "").toUpperCase(),
      String(entry?.raw_hex ?? ""),
      String(entry?.source ?? ""),
      entry?.latency_ms ?? "",
    ]);
  }

  static mergeTraceHistory(existing, incoming, limit = LOCAL_HISTORY_LIMIT) {
    const unique = new Map();
    for (const entry of [...existing, ...incoming]) {
      const key = this.entryKey(entry);
      if (!unique.has(key)) unique.set(key, entry);
    }
    const merged = [...unique.values()];
    merged.sort((left, right) => {
      const leftTime = Date.parse(left.timestamp);
      const rightTime = Date.parse(right.timestamp);
      if (Number.isFinite(leftTime) && Number.isFinite(rightTime) && leftTime !== rightTime) {
        return leftTime - rightTime;
      }
      return 0;
    });
    const boundedLimit = Math.max(0, Math.floor(Number(limit) || 0));
    return boundedLimit === 0 ? [] : merged.slice(-boundedLimit);
  }

  static _deviceName(slave, devices) {
    const device = devices?.find((item) => item.slave === slave);
    return device ? String(device.model).toUpperCase() : "Unknown";
  }

  static _registerName(address) {
    return ({0:"Indoor temperature / discovery",4:"Heat capability",5:"Indoor unit type",6:"Capacity",7:"Series 1",8:"Series 2",9:"SAAnet",10:"Outdoor Temperature",15:"Current",32:"Power",33:"Mode",34:"Fan",35:"Target Temperature",56:"Accumulated Energy",57:"Maintenance runtime",58:"Filter runtime",64:"Power",65:"Mode",66:"Fan",67:"Indoor Temperature",68:"Error"})[address] || "Raw Modbus";
  }

  static decodeRequest(bytes) {
    if (!Array.isArray(bytes) || bytes.length < 6) return [];
    const [slave, fn] = bytes, start = bytes[2]*256+bytes[3], value = bytes[4]*256+bytes[5];
    const hex = n => `0x${n.toString(16).padStart(4,"0").toUpperCase()}`;
    const rows = [["Slave",String(slave)],["Function",`FC${fn.toString(16).padStart(2,"0").toUpperCase()}`]];
    if (fn === 3 || fn === 16) {
      rows.push(["Register",`${hex(start)}–${hex(start+value-1)}`],["Count",String(value)],["Purpose",start===64 && value===5 ? "Climate state" : this._registerName(start)]);
      if (fn===16 && bytes.length===9+value*2) for(let i=0;i<value;i++) rows.push([this._registerName(start+i),String(bytes[7+i*2]*256+bytes[8+i*2])]);
    } else if(fn===6) rows.push(["Register",hex(start)],["Purpose",this._registerName(start)],["Raw",String(value)]);
    else rows.push(["Purpose","Raw Modbus"]);
    return rows;
  }

  static decodeResponse(bytes, request=[]) {
    if (!Array.isArray(bytes) || bytes.length < 5) return [];
    if(bytes[1]&128) return [["Exception",EXCEPTION_MEANINGS[bytes[2]] || String(bytes[2])]];
    const rows = [], start = request.length>=6 ? request[2]*256+request[3] : null;
    if(bytes[1]===3 && bytes.length===bytes[2]+5 && bytes[2]%2===0) {
      for(let i=0;i<bytes[2]/2;i++) {
        const address=start===null ? null : start+i, raw=bytes[3+i*2]*256+bytes[4+i*2];
        let converted=String(raw);
        if(address===56 && raw<=40000) converted=`${raw} → ${raw/10} kWh`;
        if(address===15) converted=`${raw} → ${raw/10} A`;
        if(address===65) converted=`${raw} (${["Cool","Dry","Fan","Auto","Heat"][raw] || "Unknown"})`;
        if(address===66) converted=`${raw} (${({5:"Auto",4:"High",3:"Medium",1:"Low",7:"Quiet"})[raw] || "Unknown"})`;
        rows.push([address===null ? "Raw Modbus" : this._registerName(address),converted]);
      }
    } else if(bytes[1]===6 || bytes[1]===16) rows.push(["Response","Write acknowledged"]);
    return rows;
  }

  static decodeTransaction(transaction) {
    const request=this.parseHex(transaction.tx?.raw_hex), response=this.parseHex(transaction.rx?.raw_hex);
    const status=transaction.statuses?.at(-1);
    return [...this.decodeRequest(request),...this.decodeResponse(response,request),["Result",status?.direction || "Pending"],...(status?.latency_ms == null ? [] : [["Latency",`${status.latency_ms} ms`]])];
  }
}
customElements.define("hitachi-rs485-monitor", HitachiRS485Monitor);
