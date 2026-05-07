(function () {
  const $ = (id) => document.getElementById(id);

  let selectorTag = "代理选择";
  let lastNodeNames = [];
  let currentImportVault = "";
  let currentImportPassword = "";
  let currentNodeName = "";
  let isAutoSwitching = false;
  let autoSwitchTimer = null;
  let isSwitching = false;
  let currentMeta = null;
  let currentSelectorData = null;
  let trafficSSE = null;
  let subToken = "";
  let csrfToken = "";
  let connsTimer = null;
  const trafficHistory = { up: [], down: [] };
  const MAX_TRAFFIC_POINTS = 30;

  function setGlobalLoading(on, text = "加载中...") {
    const el = $("global-loader");
    const txt = $("global-loader-text");
    if (!el) return;
    if (txt) txt.textContent = text;
    el.classList.toggle("hidden", !on);
    el.setAttribute("aria-hidden", !on);
  }

  const CACHE_KEY = "nethub_speed_cache";
  function getSpeedCache() {
    try {
      return JSON.parse(localStorage.getItem(CACHE_KEY) || "{}");
    } catch {
      return {};
    }
  }
  function updateSpeedCache(name, delayText, tier, err) {
    const cache = getSpeedCache();
    // 限制缓存有效期为 24 小时
    cache[name] = { delayText, tier, err, ts: Date.now() };
    localStorage.setItem(CACHE_KEY, JSON.stringify(cache));
  }

  function attachPasswordToggle(input) {
    if (!input || (input.type !== "password" && input.type !== "text")) return;
    if (input.dataset.hasToggle) return;
    input.dataset.hasToggle = "true";

    const wrap = document.createElement("div");
    wrap.className = "password-input-wrap";
    input.parentNode.insertBefore(wrap, input);
    wrap.appendChild(input);

    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "password-toggle-btn";
    btn.title = "显示/隐藏密码";
    btn.tabIndex = -1; // 避免 tab 键干扰主流程

    const eyeOpen = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path><circle cx="12" cy="12" r="3"></circle></svg>';
    const eyeClosed = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"></path><line x1="1" y1="1" x2="23" y2="23"></line></svg>';
    
    btn.innerHTML = eyeClosed;
    
    btn.addEventListener("click", (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      const isPassword = input.type === "password";
      input.type = isPassword ? "text" : "password";
      btn.innerHTML = isPassword ? eyeOpen : eyeClosed;
    });
    wrap.appendChild(btn);
  }

  window.hideModal = (id) => {
    const el = $(id);
    if (el) el.classList.add("hidden");
  };
  window.showModal = (id) => {
    const el = $(id);
    if (el) el.classList.remove("hidden");
  };

  async function api(path, opts = {}) {
    const showLoading = opts.loading;
    if (showLoading) setGlobalLoading(true, typeof showLoading === "string" ? showLoading : "处理中...");
    const { headers: optHeaders, loading, ...fetchOpts } = opts;
    const method = (fetchOpts.method || "GET").toUpperCase();
    const headers = { "Content-Type": "application/json", ...(optHeaders || {}) };
    if (csrfToken && !["GET", "HEAD", "OPTIONS"].includes(method)) {
      headers["X-CSRF-Token"] = csrfToken;
    }
    try {
      const r = await fetch(path, {
        credentials: "same-origin",
        ...fetchOpts,
        headers,
      });
      const text = await r.text();
      let data = null;
      try {
        data = text ? JSON.parse(text) : null;
      } catch {
        data = { detail: text };
      }
      if (!r.ok || (data && data.ok === false)) {
        const err = new Error(data?.detail || r.statusText || "请求失败");
        err.status = r.status;
        err.data = data;
        throw err;
      }
      return data;
    } finally {
      if (showLoading) setGlobalLoading(false);
    }
  }

  function show(el, on) {
    el.classList.toggle("hidden", !on);
  }

  function setTableMessage(tbody, colspan, message, color = "var(--muted)") {
    tbody.replaceChildren();
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = colspan;
    td.style.textAlign = "center";
    td.style.color = color;
    td.style.padding = "2rem";
    td.textContent = message;
    tr.appendChild(td);
    tbody.appendChild(tr);
  }

  function appendText(parent, tag, text, styles = {}) {
    const el = document.createElement(tag);
    el.textContent = text == null ? "" : String(text);
    Object.assign(el.style, styles);
    parent.appendChild(el);
    return el;
  }

  const TAB_KEY = "current_panel_tab";

  function setTab(name) {
    localStorage.setItem(TAB_KEY, name);
    const node = $("panel-node");
    const proxies = $("panel-proxies");
    const imp = $("panel-import");
    const logs = $("panel-logs");
    const conns = $("panel-conns");
    document.querySelectorAll(".nav-item").forEach((b) => {
      const on = b.dataset.tab === name;
      b.classList.toggle("active", on);
      if (on) b.setAttribute("aria-current", "page");
      else b.removeAttribute("aria-current");
    });
    show(node, name === "node");
    show(proxies, name === "proxies");
    show(imp, name === "import");
    show(logs, name === "logs");
    show(conns, name === "conns");
    if (name === "logs") loadPanelLogs();
    if (name === "conns") {
      loadPanelConns();
      startConnsInterval();
    } else {
      stopConnsInterval();
    }
    if (name === "node" || name === "proxies") {
      loadMain(currentMeta).catch(() => {});
    }
    if (name === "node") {
      initTrafficSSE();
    } else {
      stopTrafficSSE();
    }
  }

  function startConnsInterval() {
    if (connsTimer) return;
    connsTimer = setInterval(() => {
      loadPanelConns(true);
    }, 5000);
  }

  function stopConnsInterval() {
    if (connsTimer) {
      clearInterval(connsTimer);
      connsTimer = null;
    }
  }

  async function loadPanelConns(silent = false) {
    const tbody = $("conns-tbody");
    if (!tbody) return;
    const searchVal = ($("conn-search") ? $("conn-search").value : "").trim().toLowerCase();
    try {
      if (!silent && !tbody.children.length) {
        setTableMessage(tbody, 8, "正在拉取实时连接...");
      }
      const res = await api("/api/connections");
      const list = res.connections || [];
      const filtered = list.filter(c => {
        const metadata = c.metadata || {};
        if (!searchVal) return true;
        const host = (metadata.host || "").toLowerCase();
        const dstIp = (metadata.destinationIP || "").toLowerCase();
        const srcIp = (metadata.sourceIP || "").toLowerCase();
        return host.includes(searchVal) || dstIp.includes(searchVal) || srcIp.includes(searchVal);
      });
      const badge = $("conns-count-badge");
      if (badge) badge.textContent = filtered.length;
      tbody.replaceChildren();
      if (!filtered.length) {
        setTableMessage(tbody, 8, searchVal ? "没有找到符合搜索条件的活动连接" : "当前暂无活跃代理连接");
        return;
      }
      filtered.forEach((c, idx) => {
        const metadata = c.metadata || {};
        const tr = document.createElement("tr");

        // 序号列
        const tdIdx = document.createElement("td");
        tdIdx.textContent = idx + 1;
        tdIdx.style.textAlign = "center";
        tdIdx.style.color = "var(--muted)";
        tr.appendChild(tdIdx);

        const tdType = document.createElement("td");
        const network = (metadata.network || "TCP").toUpperCase();
        const type = metadata.type || "";
        appendText(tdType, "span", network, {
          fontSize: "0.75rem",
          padding: "2px 6px",
          borderRadius: "4px",
          fontWeight: "bold",
          background: network === "UDP" ? "#eab308" : "#3b82f6",
          color: "#fff",
        });
        appendText(tdType, "div", type, { fontSize: "0.75rem", color: "var(--muted)", marginTop: "2px" });
        tr.appendChild(tdType);

        const tdSrc = document.createElement("td");
        tdSrc.style.fontFamily = "monospace";
        tdSrc.style.fontSize = "0.85rem";
        tdSrc.textContent = `${metadata.sourceIP || ""}:${metadata.sourcePort || ""}`;
        tr.appendChild(tdSrc);

        const tdDst = document.createElement("td");
        tdDst.style.fontWeight = "500";
        const host = metadata.host;
        const destIP = metadata.destinationIP || "";
        const port = metadata.destinationPort || "";
        if (host) {
          appendText(tdDst, "div", host, { wordBreak: "break-all" });
          appendText(tdDst, "div", `${destIP}:${port}`, {
            fontSize: "0.75rem",
            color: "var(--muted)",
            fontFamily: "monospace",
          });
        } else {
          appendText(tdDst, "span", `${destIP}:${port}`, { fontFamily: "monospace", fontSize: "0.85rem" });
        }
        tr.appendChild(tdDst);

        const tdRule = document.createElement("td");
        const ruleName = c.rule || "Match";
        const payload = c.rulePayload ? ` [${c.rulePayload}]` : "";
        let ruleBg = "#4b5563";
        if (ruleName.toLowerCase().includes("direct") || ruleName.toLowerCase() === "match") ruleBg = "#10b981";
        else if (ruleName.toLowerCase().includes("reject") || ruleName.toLowerCase().includes("block")) ruleBg = "#ef4444";
        else if (ruleName.toLowerCase() !== "match") ruleBg = "var(--primary)";
        appendText(tdRule, "span", ruleName, {
          display: "inline-block",
          fontSize: "0.75rem",
          padding: "2px 6px",
          borderRadius: "4px",
          color: "#fff",
          fontWeight: "500",
          background: ruleBg,
        });
        appendText(tdRule, "div", payload, {
          fontSize: "0.75rem",
          color: "var(--muted)",
          marginTop: "2px",
          wordBreak: "break-all",
        });
        tr.appendChild(tdRule);

        const tdNode = document.createElement("td");
        tdNode.style.fontWeight = "500";
        const chain = c.chains || [];
        const outNodeName = chain[chain.length - 1] || "direct";
        appendText(tdNode, "span", outNodeName, { color: outNodeName === "direct" ? "var(--muted)" : "var(--primary)" });
        tr.appendChild(tdNode);

        const tdTraffic = document.createElement("td");
        tdTraffic.style.fontFamily = "monospace";
        tdTraffic.style.fontSize = "0.85rem";
        tdTraffic.textContent = `${formatBytesSimple(c.upload)} ⬆ / ${formatBytesSimple(c.download)} ⬇`;
        tr.appendChild(tdTraffic);

        const tdOpt = document.createElement("td");
        tdOpt.style.textAlign = "center";
        const btnClose = document.createElement("button");
        btnClose.type = "button";
        btnClose.className = "btn-icon-del";
        btnClose.title = "断开此连接";
        btnClose.style.padding = "4px";
        btnClose.style.background = "none";
        btnClose.style.border = "none";
        btnClose.style.cursor = "pointer";
        btnClose.innerHTML = '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="color: var(--danger);"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>';
        btnClose.onclick = async () => {
          try {
            await api(`/api/connections/${c.id}`, { method: "DELETE" });
            tr.remove();
            const currBadge = $("conns-count-badge");
            if (currBadge) currBadge.textContent = Math.max(0, parseInt(currBadge.textContent) - 1);
          } catch (err) {
            console.error("断开连接失败", err);
          }
        };
        tdOpt.appendChild(btnClose);
        tr.appendChild(tdOpt);
        tbody.appendChild(tr);
      });
    } catch (e) {
      if (!silent) {
        setTableMessage(tbody, 8, `加载连接失败: ${e.message || String(e)}`, "var(--danger)");
      }
    }
  }

  function formatBytesSimple(bytes) {
    if (bytes === 0) return "0 B";
    const k = 1024;
    const sizes = ["B", "KB", "MB", "GB"];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + " " + sizes[i];
  }

  async function loadPanelLogs() {
    const wrap = $("panel-log-table-wrap");
    if (!wrap) return;
    wrap.textContent = "加载中…";
    try {
      const data = await api("/api/panel-logs");
      const entries = (data.entries || []).slice();
      if (!entries.length) {
        wrap.textContent = "（暂无记录）";
        return;
      }

      function opLabel(op) {
        const raw = (op || "").trim();
        if (!raw) return "-";
        const map = {
          login_rate_limited: "登录限流",
          login_failed: "登录失败",
          login_ok: "登录成功",
          logout: "退出登录",
          vault_import: "导入节点库",
          switch_node: "切换节点",
        };
        return map[raw] || raw; // 若后端已写中文，直接显示
      }

      // 时间倒序（t 为 "YYYY-MM-DD HH:MM:SS" 时可按字符串排序；兼容缺失值）
      entries.sort((a, b) => String(b?.t || "").localeCompare(String(a?.t || "")));

      const table = document.createElement("table");
      table.className = "panel-log-table";

      const thead = document.createElement("thead");
      const thr = document.createElement("tr");
      const headers = ["序号", "时间", "IP", "用户", "操作", "详情"];
      for (const h of headers) {
        const th = document.createElement("th");
        th.textContent = h;
        if (h === "序号") {
          th.style.textAlign = "center";
          th.style.width = "4.5rem";
        }
        thr.appendChild(th);
      }
      thead.appendChild(thr);
      table.appendChild(thead);

      const tbody = document.createElement("tbody");
      let idx = 1;
      for (const e of entries) {
        const tr = document.createElement("tr");
        const t = e.t || "";
        const ip = e.ip || "-";
        const user = e.user || "-";
        const op = opLabel(e.op);
        const msg = e.msg || "";

        // 序号列
        const tdIdx = document.createElement("td");
        tdIdx.textContent = idx++;
        tdIdx.style.textAlign = "center";
        tdIdx.style.color = "var(--muted)";
        tr.appendChild(tdIdx);

        for (const v of [t, ip, user, op, msg]) {
          const td = document.createElement("td");
          td.textContent = v;
          tr.appendChild(td);
        }
        tbody.appendChild(tr);
      }
      table.appendChild(tbody);

      wrap.innerHTML = "";
      wrap.appendChild(table);
    } catch (e) {
      wrap.textContent = "加载失败: " + (e.message || String(e));
    }
  }

  function svgIconActivity() {
    return '<svg class="icon-svg" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg>';
  }

  function tierClass(tier) {
    if (!tier || tier === "—" || tier === "失败") return "node-m-tier is-bad";
    if (tier.startsWith("一般") || tier.startsWith("较慢")) return "node-m-tier is-warn";
    return "node-m-tier";
  }

  function setCardMetrics(card, delayText, tier, err) {
    const ddDelay = card.querySelector(".node-m-delay");
    const ddTier = card.querySelector(".node-m-tier");
    if (err) {
      ddDelay.textContent = err;
      ddTier.textContent = "—";
      ddTier.className = "node-m-tier is-bad";
      return;
    }
    ddDelay.textContent = delayText;
    ddTier.textContent = tier || "—";
    ddTier.className = tierClass(tier);
  }

  function setCardHealth(card, health) {
    const el = card && card.querySelector(".node-m-health");
    if (!el) return;
    if (!health || health.score == null) {
      el.textContent = "未知";
      el.className = "node-m-health is-warn";
      return;
    }
    el.textContent = `${health.score} · ${health.tier || "健康"}`;
    el.className = health.score >= 70 ? "node-m-health" : health.score >= 45 ? "node-m-health is-warn" : "node-m-health is-bad";
  }

  function buildNodeCard(name, nowName) {
    const art = document.createElement("article");
    art.className = "node-card" + (name === nowName ? " node-card--current" : "");
    art.dataset.name = name;
    art.setAttribute("role", "listitem");
    art.tabIndex = 0;
    art.title = "点击切换到此节点；左侧图标为测延迟";

    const head = document.createElement("div");
    head.className = "node-card-head";
    const btnPing = document.createElement("button");
    btnPing.type = "button";
    btnPing.className = "btn-icon btn-icon--ping btn-icon--ping-only";
    btnPing.title = "测延迟";
    btnPing.setAttribute("aria-label", "测延迟");
    btnPing.innerHTML = svgIconActivity();
    head.appendChild(btnPing);

    const h3 = document.createElement("h3");
    h3.className = "node-card-title";
    h3.textContent = name;
    head.appendChild(h3);
    if (name === nowName) {
      const badge = document.createElement("span");
      badge.className = "node-card-badge";
      badge.textContent = "当前";
      head.appendChild(badge);
    }
    art.appendChild(head);

    const metrics = document.createElement("div");
    metrics.className = "node-card-metrics";
    const row = document.createElement("div");
    row.className = "node-m-row";

    const itemDelay = document.createElement("span");
    itemDelay.className = "node-m-item";
    const labDelay = document.createElement("span");
    labDelay.className = "node-m-lab";
    labDelay.textContent = "延迟";
    const dd1 = document.createElement("span");
    dd1.className = "node-m-delay";
    dd1.textContent = "未测";
    itemDelay.appendChild(labDelay);
    itemDelay.appendChild(dd1);

    const sep = document.createElement("span");
    sep.className = "node-m-sep";
    sep.setAttribute("aria-hidden", "true");
    sep.textContent = "·";

    const itemTier = document.createElement("span");
    itemTier.className = "node-m-item";
    const labTier = document.createElement("span");
    labTier.className = "node-m-lab";
    labTier.textContent = "网速";
    const dd2 = document.createElement("span");
    dd2.className = "node-m-tier";
    dd2.textContent = "—";
    itemTier.appendChild(labTier);
    itemTier.appendChild(dd2);

    row.appendChild(itemDelay);
    row.appendChild(sep);
    row.appendChild(itemTier);
    metrics.appendChild(row);

    const rowHealth = document.createElement("div");
    rowHealth.className = "node-m-row";
    const itemHealth = document.createElement("span");
    itemHealth.className = "node-m-item";
    const labHealth = document.createElement("span");
    labHealth.className = "node-m-lab";
    labHealth.textContent = "健康";
    const ddHealth = document.createElement("span");
    ddHealth.className = "node-m-health is-warn";
    ddHealth.textContent = "未知";
    itemHealth.appendChild(labHealth);
    itemHealth.appendChild(ddHealth);
    rowHealth.appendChild(itemHealth);
    metrics.appendChild(rowHealth);
    art.appendChild(metrics);

    return art;
  }

  async function runDelayTests(names) {
    const hint = $("node-test-hint");
    if (!names.length) return;
    let done = 0;
    const total = names.length;
    hint.textContent = `正在测速：0/${total}`;
    $("node-cards").querySelectorAll(".node-card").forEach((c) => {
      if (names.includes(c.dataset.name)) {
        c.classList.add("is-testing");
        setCardMetrics(c, "测试中…", "—", null);
      }
    });
    const list = $("node-cards").querySelectorAll(".node-card");
    const getCard = (name) => Array.from(list).find((c) => c.dataset.name === name);

    // 并发限制，避免一次性打爆后端/Clash API
    const concurrency = 5;
    const queue = names.slice();

    async function one(name) {
      const card = getCard(name);
      try {
        const info = await api("/api/proxy-delay", {
          method: "POST",
          body: JSON.stringify({ name, timeout_ms: 12000 }),
        });
        if (card) {
          card.classList.remove("is-testing");
          if (info.error) {
            setCardMetrics(card, "失败", "—", info.error);
            setCardHealth(card, info.health);
            updateSpeedCache(name, "失败", "—", info.error);
          } else if (info.delay_ms != null) {
            const dText = `${info.delay_ms} ms`;
            setCardMetrics(card, dText, info.tier || "—", null);
            setCardHealth(card, info.health);
            updateSpeedCache(name, dText, info.tier || "—", null);
          } else {
            setCardMetrics(card, "—", info.tier || "—", info.error || "超时");
            setCardHealth(card, info.health);
            updateSpeedCache(name, "—", info.tier || "—", info.error || "超时");
          }
        }
      } catch (e) {
        if (card) {
          card.classList.remove("is-testing");
          setCardMetrics(card, "失败", "—", e.message || "请求失败");
          updateSpeedCache(name, "失败", "—", e.message || "请求失败");
        }
      } finally {
        done += 1;
        hint.textContent = `正在测速：${done}/${total}`;
      }
    }

    try {
      const workers = [];
      for (let i = 0; i < Math.min(concurrency, queue.length); i++) {
        workers.push(
          (async () => {
            while (queue.length) {
              const n = queue.shift();
              if (!n) break;
              await one(n);
            }
          })(),
        );
      }
      await Promise.all(workers);
      hint.textContent = "测速完成";
    } catch (e) {
      hint.textContent = "测速失败: " + (e.message || String(e));
    }
  }

  async function switchToNode(name) {
    const msg = $("apply-msg");
    show(msg, true);
    msg.classList.remove("err");
    msg.textContent = "正在切换…";
    try {
      await api(`/api/selector/${encodeURIComponent(selectorTag)}`, {
        method: "PUT",
        body: JSON.stringify({ name }),
        loading: "正在切换节点...",
      });
      msg.textContent = "已切换为: " + name;
      const data = await loadSelectorSummary();
      fillNodeCards(data);
      await refreshHealth();
    } catch (e) {
      msg.classList.add("err");
      msg.textContent =
        typeof e.data?.detail === "string"
          ? e.data.detail
          : e.message || "切换失败";
    }
  }

  function fillNodeCards(data) {
    currentSelectorData = data;
    const all = data.all || [];
    const now = data.now || "";
    
    let filtered = all.slice();
    const q = ($("node-search") ? $("node-search").value : "").trim().toLowerCase();
    if (q) {
      filtered = filtered.filter(n => n.toLowerCase().includes(q));
    }
    
    const sort = $("node-sort") ? $("node-sort").value : "default";
    const cache = getSpeedCache();
    if (sort === "name") {
      filtered.sort((a, b) => a.localeCompare(b));
    } else if (sort === "latency") {
      filtered.sort((a, b) => {
        const da = cache[a] ? (parseInt(cache[a].delayText) || 9999) : 9999;
        const db = cache[b] ? (parseInt(cache[b].delayText) || 9999) : 9999;
        return da - db;
      });
    }

    currentNodeName = now;
    lastNodeNames = filtered; 
    const wrap = $("node-cards");
    wrap.innerHTML = "";
    $("selector-tag").textContent = data.tag || selectorTag;

    const nowTs = Date.now();

    for (const name of filtered) {
      const card = buildNodeCard(name, now);
      wrap.appendChild(card);
      setCardHealth(card, data.health && data.health[name]);

      const cached = cache[name];
      // 24小时内有效
      if (cached && nowTs - cached.ts < 86400000) {
        setCardMetrics(card, cached.delayText, cached.tier, cached.err);
      }
    }
    show($("selector-section"), true);
    $("stat-node").textContent = now || "未连接";
  }

  async function loadMeta() {
    const m = await api("/api/meta");
    selectorTag = m.selector_tag || selectorTag;
    csrfToken = m.csrf_token || csrfToken;
    currentMeta = m;
    
    // 填充路由模式
    const sel = $("route-mode-select");
    if (sel && m.route_modes) {
      sel.innerHTML = "";
      for (const [k, v] of Object.entries(m.route_modes)) {
        const opt = document.createElement("option");
        opt.value = k;
        opt.textContent = v;
        if (k === "bypass_cn") opt.selected = true;
        sel.appendChild(opt);
      }
      sel.onchange = handleRouteModeChange;

      // 更新状态卡片的只读文本
      const statRouteMode = $("stat-route-mode");
      if (statRouteMode) {
        const selectedText = sel.options[sel.selectedIndex]?.text || "绕过大陆";
        statRouteMode.textContent = `分流模式: ${selectedText}`;
      }
    }
    
    return m;
  }

  async function handleRouteModeChange(ev) {
    const mode = ev.target.value;
    const selectedText = ev.target.options[ev.target.selectedIndex].text;
    const name = "default"; // 默认库
    const pw = await verifyVaultPassword(name, "重构配置验证", `更改路由模式为“${selectedText}”需要验证密码：`);
    if (!pw) {
       // 恢复原状
       loadMeta().catch(() => {});
       return;
    }
    try {
      await api("/api/rebuild", {
        method: "POST",
        body: JSON.stringify({ vault_password: pw, route_mode: mode }),
        loading: "正在重新部署规则..."
      });
      // 更新状态卡片的只读文本
      const statRouteMode = $("stat-route-mode");
      if (statRouteMode) {
        statRouteMode.textContent = `分流模式: ${selectedText}`;
      }
      showConfirmModal({ title: "重构成功", desc: "路由模式已更新，内核已重载。", confirmText: "知道了", hideCancel: true }, () => {});
    } catch (e) {
      // 恢复原状
      loadMeta().catch(() => {});
      showConfirmModal({ title: "重构失败", desc: e.message || "无法应用新模式", confirmText: "重试" }, () => {});
    }
  }

  function initTrafficSSE() {
    if (trafficSSE) return;
    trafficSSE = new EventSource("/api/traffic");
    trafficSSE.onmessage = (ev) => {
      try {
        const data = JSON.parse(ev.data);
        updateTrafficMetrics(data);
      } catch (e) {}
    };
    trafficSSE.onerror = () => {
      stopTrafficSSE();
    };
  }

  function stopTrafficSSE() {
    if (trafficSSE) {
      trafficSSE.close();
      trafficSSE = null;
    }
  }

  function formatBytes(bytes) {
    if (bytes === 0) return "0.00 B/s";
    const k = 1024;
    const sizes = ["B/s", "KB/s", "MB/s", "GB/s"];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + " " + sizes[i];
  }

  function updateTrafficMetrics(data) {
    const up = data.up || 0;
    const down = data.down || 0;
    $("stat-up").textContent = formatBytes(up);
    $("stat-down").textContent = formatBytes(down);
    
    trafficHistory.up.push(up);
    trafficHistory.down.push(down);
    if (trafficHistory.up.length > MAX_TRAFFIC_POINTS) {
      trafficHistory.up.shift();
      trafficHistory.down.shift();
    }
    
    drawSparkline("chart-up", trafficHistory.up, "#2563eb");
    drawSparkline("chart-down", trafficHistory.down, "#16a34a");
  }

  function drawSparkline(canvasId, data, color) {
    const canvas = $(canvasId);
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    canvas.width = rect.width * dpr;
    canvas.height = rect.height * dpr;
    ctx.scale(dpr, dpr);

    ctx.clearRect(0, 0, rect.width, rect.height);
    if (data.length < 2) return;

    const max = Math.max(...data, 1024);
    const step = rect.width / (MAX_TRAFFIC_POINTS - 1);
    
    ctx.beginPath();
    ctx.strokeStyle = color;
    ctx.lineWidth = 2;
    ctx.lineJoin = "round";
    
    data.forEach((val, i) => {
      const x = i * step;
      const y = rect.height - (val / max) * rect.height * 0.8 - 5;
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.stroke();
    
    // 填充渐变
    ctx.lineTo((data.length - 1) * step, rect.height);
    ctx.lineTo(0, rect.height);
    ctx.closePath();
    const grad = ctx.createLinearGradient(0, 0, 0, rect.height);
    grad.addColorStop(0, color + "44");
    grad.addColorStop(1, color + "00");
    ctx.fillStyle = grad;
    ctx.fill();
  }

  async function loadVaultStatus() {
    const el = $("vault-status");
    try {
      const s = await api("/api/vault/status");
      subToken = s.sub_token || "";
      const total = typeof s.vault_count === "number" ? s.vault_count : (s.vaults || []).length;
      const enabled = typeof s.enabled_count === "number" ? s.enabled_count : total;
      const subscriptionCount = (s.vaults || []).filter((v) => v.source_kind === "subscription" && v.source_url).length;
      el.textContent = s.has_vault
        ? `已存在节点库 ${total} 个（启用 ${enabled} 个，订阅源 ${subscriptionCount} 个）`
        : "尚未导入节点库";
    } catch (e) {
      if (e.status === 401) {
        window.location.href = "/login";
        return;
      }
      el.textContent = "无法读取库状态: " + (e.message || String(e));
    }
  }

  function showConfirmModal({ title, desc, confirmText, hideCancel, cancelText }, onConfirm, onCancelCb) {
    const overlay = $("modal-overlay");
    const elTitle = $("modal-title");
    const elDesc = $("modal-desc");
    const elExtra = $("modal-extra");
    const btnCancel = $("modal-cancel");
    const btnConfirm = $("modal-confirm");
    if (!overlay || !btnCancel || !btnConfirm) return;

    elTitle.textContent = title || "确认操作";
    elDesc.textContent = desc || "";
    btnConfirm.textContent = confirmText || "确认";
    btnCancel.textContent = cancelText || "取消";
    show(btnCancel, !hideCancel);

    if (elExtra) {
      elExtra.innerHTML = "";
      elExtra.classList.add("hidden");
    }

    let closed = false;
    const cleanup = () => {
      if (closed) return;
      closed = true;
      show(overlay, false);
      btnCancel.removeEventListener("click", onCancel);
      btnConfirm.removeEventListener("click", onOk);
      document.removeEventListener("keydown", onKey);
    };

    const onCancel = () => {
      cleanup();
      if (onCancelCb) onCancelCb();
    };
    const onOk = async () => {
      btnConfirm.disabled = true;
      try {
        await onConfirm();
      } finally {
        btnConfirm.disabled = false;
        cleanup();
      }
    };
    const onKey = (ev) => {
      if (ev.key === "Escape") cleanup();
    };

    btnCancel.addEventListener("click", onCancel);
    btnConfirm.addEventListener("click", onOk);
    document.addEventListener("keydown", onKey);

    show(overlay, true);
    btnCancel.focus();
  }

  function showFormModal({ title, desc, confirmText, fields }, onSubmit) {
    const overlay = $("modal-overlay");
    const elTitle = $("modal-title");
    const elDesc = $("modal-desc");
    const elExtra = $("modal-extra");
    const btnCancel = $("modal-cancel");
    const btnConfirm = $("modal-confirm");
    if (!overlay || !btnCancel || !btnConfirm || !elExtra) return;

    elTitle.textContent = title || "操作";
    elDesc.textContent = desc || "";
    btnConfirm.textContent = confirmText || "确认";

    elExtra.innerHTML = "";
    elExtra.classList.remove("hidden");

    const inputs = {};
    for (const f of fields || []) {
      const wrap = document.createElement("label");
      wrap.className = "field";
      const lab = document.createElement("span");
      lab.className = "field-label";
      lab.textContent = f.label;
      const input = document.createElement("input");
      input.type = f.type || "text";
      input.id = f.id;
      input.autocomplete = f.autocomplete || (f.type === "password" ? "new-password" : "off");
      if (f.placeholder) input.placeholder = f.placeholder;
      if (f.required) input.required = true;
      if (f.value) input.value = f.value;
      wrap.appendChild(lab);
      wrap.appendChild(input);
      elExtra.appendChild(wrap);
      inputs[f.id] = input;
      if (f.type === "password") attachPasswordToggle(input);
    }

    let closed = false;
    const cleanup = () => {
      if (closed) return;
      closed = true;
      show(overlay, false);
      btnCancel.removeEventListener("click", onCancel);
      btnConfirm.removeEventListener("click", onOk);
      document.removeEventListener("keydown", onKey);
    };
    const onCancel = () => cleanup();
    const onOk = async () => {
      btnConfirm.disabled = true;
      try {
        const values = {};
        for (const k of Object.keys(inputs)) values[k] = inputs[k].value;
        cleanup();
        await onSubmit(values);
      } finally {
        btnConfirm.disabled = false;
      }
    };
    const onKey = (ev) => {
      if (ev.key === "Escape") cleanup();
      if (ev.key === "Enter" && (ev.ctrlKey || ev.metaKey)) onOk();
    };

    btnCancel.addEventListener("click", onCancel);
    btnConfirm.addEventListener("click", onOk);
    document.addEventListener("keydown", onKey);

    show(overlay, true);
    const first = fields && fields.length ? inputs[fields[0].id] : btnCancel;
    if (first && first.focus) first.focus();
  }

  async function verifyVaultPassword(name, title = "身份验证", desc = "") {
    return new Promise((resolve) => {
      showFormModal(
        {
          title: title,
          desc: desc || `进行此操作需要验证节点库“${name}”的密码：`,
          confirmText: "验证密码",
          fields: [
            {
              id: "pw",
              label: "库密码",
              type: "password",
              required: true,
            },
          ],
        },
        async (vals) => {
          const pw = (vals.pw || "").trim();
          if (!pw) {
            resolve(null);
            return;
          }
          try {
            await api("/api/vault/verify", {
              method: "POST",
              body: JSON.stringify({ name, password: pw }),
            });
            showConfirmModal({
              title: "验证成功",
              desc: "身份验证已通过，点击继续后续操作。",
              confirmText: "继续"
            }, () => {
              resolve(pw);
            }, () => {
              resolve(null);
            });
          } catch (e) {
            showConfirmModal({
              title: "验证失败",
              desc: e.data?.detail || e.message || "密码不正确，请重新输入",
              confirmText: "重试"
            }, () => {
              // 此处 resolve(null) 触发外层重新打开密码框或者停止
              resolve(null);
            }, () => {
              resolve(null);
            });
          }
        }
      );
    });
  }

  const VAULT_TARGET_KEY = "vault_target_name";

  function getVaultTarget() {
    const v = (localStorage.getItem(VAULT_TARGET_KEY) || "").trim();
    return v || "";
  }

  function setVaultTarget(name) {
    localStorage.setItem(VAULT_TARGET_KEY, name);
  }

  async function renderVaultManageTable() {
    const tbody = $("vault-manage-tbody");
    if (!tbody) return;
    try {
      const r = await api("/api/vaults");
      const vaults = r.vaults || [];
      tbody.innerHTML = "";
      let idx = 1;
      for (const v of vaults) {
        const tr = document.createElement("tr");
        tr.dataset.name = v.name;

        // 1. 序号
        const tdIdx = document.createElement("td");
        tdIdx.textContent = idx++;
        tdIdx.style.textAlign = "center";
        tdIdx.style.color = "var(--muted)";

        // 2. 名称
        const tdName = document.createElement("td");
        tdName.textContent = v.name;
        tdName.style.fontWeight = "500";

        // 3. 节点数量
        const tdCount = document.createElement("td");
        tdCount.style.textAlign = "center";
        tdCount.style.fontWeight = "600";
        tdCount.style.color = "var(--primary)";
        tdCount.dataset.count = String(v.node_count || 0);
        tdCount.textContent = v.node_count || 0;
        if (typeof v.duplicate_count === "number" && v.duplicate_count > 0) {
          appendText(tdCount, "div", `去重 ${v.duplicate_count} 条`, {
            marginTop: "2px",
            fontSize: "0.72rem",
            color: "var(--muted)",
            fontWeight: "400",
          });
        }
        if (v.source_kind === "subscription" && v.source_url) {
          appendText(tdCount, "div", "订阅刷新", {
            marginTop: "2px",
            fontSize: "0.72rem",
            color: "#0f766e",
            fontWeight: "500",
          });
        }

        // 4. 节点管理
        const tdNodeManage = document.createElement("td");
        tdNodeManage.style.textAlign = "center";
        const grpNode = document.createElement("div");
        grpNode.className = "vault-manage-actions is-center";
        
        const btnImp = document.createElement("button");
        btnImp.type = "button";
        btnImp.className = "btn-secondary btn-mini vault-import";
        btnImp.textContent = "导入";

        const btnView = document.createElement("button");
        btnView.type = "button";
        btnView.className = "btn-secondary btn-mini vault-view";
        btnView.textContent = "查看";

        grpNode.appendChild(btnImp);
        grpNode.appendChild(btnView);
        if (v.source_kind === "subscription" && v.source_url) {
          const btnRefresh = document.createElement("button");
          btnRefresh.type = "button";
          btnRefresh.className = "btn-secondary btn-mini vault-refresh";
          btnRefresh.textContent = "刷新";
          grpNode.appendChild(btnRefresh);
        }
        tdNodeManage.appendChild(grpNode);

        // 5. 启用 (Switch)
        const tdOn = document.createElement("td");
        tdOn.style.textAlign = "center";
        const label = document.createElement("label");
        label.className = "switch";
        const inputId = `vault-cb-${v.name}`;
        label.setAttribute("for", inputId);
        const cb = document.createElement("input");
        cb.type = "checkbox";
        cb.id = inputId;
        cb.checked = !!v.enabled;
        cb.className = "vault-enable";
        const slider = document.createElement("span");
        slider.className = "slider";
        label.appendChild(cb);
        label.appendChild(slider);
        tdOn.appendChild(label);

        // 6. 操作
        const tdAct = document.createElement("td");
        const act = document.createElement("div");
        act.className = "vault-manage-actions is-center";

        const btnRen = document.createElement("button");
        btnRen.type = "button";
        btnRen.className = "btn-secondary btn-mini vault-rename";
        btnRen.textContent = "重命名";

        const btnDel = document.createElement("button");
        btnDel.type = "button";
        btnDel.className = "btn-secondary btn-mini vault-delete";
        btnDel.textContent = "删除";

        act.appendChild(btnRen);
        act.appendChild(btnDel);
        tdAct.appendChild(act);

        tr.appendChild(tdIdx);
        tr.appendChild(tdName);
        tr.appendChild(tdCount);
        tr.appendChild(tdNodeManage);
        tr.appendChild(tdOn);
        tr.appendChild(tdAct);
        tbody.appendChild(tr);
      }
    } catch (e) {
      setTableMessage(tbody, 6, `无法加载节点库列表: ${e.message || String(e)}`, "var(--danger)");
    }
  }

  async function toggleVault(name, enabled) {
    await api("/api/vaults/toggle", { method: "POST", body: JSON.stringify({ name, enabled }) });
    await loadVaultStatus();
    await renderVaultManageTable();
  }

  async function renameVault(name) {
    const pw = await verifyVaultPassword(name, "重命名身份验证", `重命名节点库“${name}”前，请先验证密码：`);
    if (!pw) return;

    showFormModal(
      {
        title: "重命名节点库",
        desc: `请输入“${name}”的新名称：`,
        confirmText: "确认重命名",
        fields: [
          {
            id: "new_name",
            label: "新名称",
            type: "text",
            placeholder: "支持中文、字母、数字、空格等",
            value: name,
            required: true,
          },
        ],
      },
      async (vals) => {
        const next = (vals.new_name || "").trim();
        if (!next || next === name) return;
        try {
          await api("/api/vaults/rename", {
            method: "POST",
            body: JSON.stringify({ old_name: name, new_name: next, password: pw }),
          });
          await loadVaultStatus();
          await renderVaultManageTable();
          if (getVaultTarget() === name) setVaultTarget(next);
        } catch (e) {
          showConfirmModal({
            title: "操作失败",
            desc: e.data?.detail || e.message || "重命名失败",
            confirmText: "知道了"
          }, () => {});
        }
      },
    );
  }

  async function deleteVault(name) {
    const pw = await verifyVaultPassword(name, "删除身份验证", `删除节点库“${name}”前，请先验证密码：`);
    if (!pw) return;

    showConfirmModal(
      {
        title: "确认删除节点库",
        desc: `此操作将永久删除节点库“${name}”及其所有数据。确定要继续吗？`,
        confirmText: "永久删除",
      },
      async () => {
        try {
          await api("/api/vaults/delete", {
            method: "POST",
            body: JSON.stringify({ name, password: pw })
          });
          await loadVaultStatus();
          await renderVaultManageTable();
          if (getVaultTarget() === name) setVaultTarget("");
        } catch (e) {
          showConfirmModal({
            title: "操作失败",
            desc: e.data?.detail || e.message || "删除失败",
            confirmText: "知道了"
          }, () => {});
        }
      },
    );
  }

  async function exportVaultContent(name, callback) {
    showFormModal(
      {
        title: "身份验证",
        desc: `请输入节点库“${name}”的密码以解密：`,
        confirmText: "确认",
        fields: [
          {
            id: "pw",
            label: "库密码",
            type: "password",
            required: true,
          },
        ],
      },
      async (vals) => {
        const pw = (vals.pw || "").trim();
        if (!pw) return;
        try {
          // 1. 先进行密码验证
          await api("/api/vault/verify", {
            method: "POST",
            body: JSON.stringify({ name, password: pw }),
          });
          // 2. 验证通过后执行导出
          const r = await api("/api/vault/export", {
            method: "POST",
            body: JSON.stringify({ name, password: pw }),
          });
          callback({ urls: r.urls, password: pw });
        } catch (e) {
          showConfirmModal({
            title: "验证失败",
            desc: e.data?.detail || e.message || "密码错误或无法连接服务器",
            confirmText: "知道了"
          }, () => {});
        }
      },
    );
  }

  async function viewVaultNodes(name) {
    await exportVaultContent(name, async (res) => {
      renderVaultNodesList(name, res.urls, res.password);
    });
  }

  function renderVaultNodesList(vaultName, urls, password) {
    $("view-vault-name").textContent = vaultName;
    const wrap = $("view-vault-content");
    wrap.innerHTML = "";
    if (!urls || !urls.length) {
      wrap.innerHTML = '<p style="padding: 2rem; text-align: center; color: var(--muted);">（库内暂无节点）</p>';
    } else {
      const ul = document.createElement("ul");
      ul.className = "vault-nodes-ul";
      urls.forEach((u, idx) => {
        const li = document.createElement("li");
        li.className = "vault-node-item";
        
        let label = u;
        try {
          const url = new URL(u);
          if (url.hash) label = decodeURIComponent(url.hash.substring(1));
        } catch (_) {}

        const span = document.createElement("span");
        span.textContent = label;
        span.title = u;
        span.className = "node-name";

        const btnDel = document.createElement("button");
        btnDel.type = "button";
        btnDel.className = "btn-icon-del";
        btnDel.title = "删除此节点";
        btnDel.innerHTML = '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path><line x1="10" y1="11" x2="10" y1="17"></line><line x1="14" y1="11" x2="14" y1="17"></line></svg>';
        
        btnDel.addEventListener("click", () => {
          showConfirmModal({
            title: "确认删除节点",
            desc: `确定要从库“${vaultName}”中删除节点“${label}”吗？`,
            confirmText: "确认删除"
          }, async () => {
            const nextUrls = urls.filter((_, i) => i !== idx);
            try {
              await api("/api/vault/import", {
                method: "POST",
                body: JSON.stringify({
                  vault_name: vaultName,
                  urls_text: nextUrls.join("\n"),
                  vault_password: password
                }),
                loading: "正在同步更改...",
              });
              renderVaultNodesList(vaultName, nextUrls, password);
              await loadVaultStatus();
              await renderVaultManageTable();
            } catch (e) {
              showConfirmModal({
                title: "同步失败",
                desc: typeof e.data?.detail === "string" ? e.data.detail : e.message || "无法同步更改到服务器",
                confirmText: "知道了"
              }, () => {});
            }
          });
        });

        li.appendChild(span);
        li.appendChild(btnDel);
        ul.appendChild(li);
      });
      wrap.appendChild(ul);
    }
    showModal("modal-vault-view");
  }

  async function createVault() {
    const msg = $("vault-msg");
    showFormModal(
      {
        title: "新建节点库",
        desc: "支持中文、字母、数字、空格、- 或 _。",
        confirmText: "创建",
        fields: [
          {
            id: "vault_name",
            label: "库名称",
            type: "text",
            placeholder: "例如: work / home / test",
            required: true,
          },
          {
            id: "vault_password",
            label: "库密码",
            type: "password",
            autocomplete: "new-password",
            required: true,
          },
        ],
      },
      async (vals) => {
        const name = (vals.vault_name || "").trim();
        const pw = (vals.vault_password || "").trim();
        if (!name || !pw) return;
        try {
          show(msg, false);
          await api("/api/vaults/create", {
            method: "POST",
            body: JSON.stringify({ name, password: pw }),
            loading: "正在创建节点库...",
          });
          await loadVaultStatus();
          await renderVaultManageTable();
          if (msg) {
            show(msg, true);
            msg.classList.remove("err");
            msg.textContent = `已成功创建节点库：${name}（已锁定初始密码）`;
          }
        } catch (e) {
          if (msg) {
            show(msg, true);
            msg.classList.add("err");
            msg.textContent = e.data?.detail || e.message || "创建失败";
          }
        }
      },
    );
  }

  async function resetVault() {
    const msg = $("vault-msg");
    showFormModal(
      {
        title: "清空全部节点库",
        desc: "该操作会删除所有节点库文件（含 default），且不可恢复。请输入【管理员密码】确认：",
        confirmText: "确认清空全部",
        fields: [
          {
            id: "admin_pw",
            label: "管理员密码",
            type: "password",
            required: true,
          },
        ],
      },
      async (vals) => {
        const pw = (vals.admin_pw || "").trim();
        if (!pw) return;
        show(msg, true);
        msg.classList.remove("err");
        msg.textContent = "正在清空…";
        try {
          await api("/api/vault/reset", {
            method: "POST",
            body: JSON.stringify({ admin_password: pw }),
            loading: "正在彻底清空所有节点库...",
          });
          msg.textContent = "所有节点库已清空。";
          await loadVaultStatus();
          await renderVaultManageTable();
          const ta = $("vault-urls");
          if (ta) ta.value = "";
        } catch (e) {
          msg.classList.add("err");
          msg.textContent = e.data?.detail || e.message || "清空失败";
        }
      },
    );
  }

  async function loadSelectorSummary() {
    return api("/api/selector-summary");
  }

  async function tryHealth() {
    return api("/api/health");
  }

  function formatUptime(seconds) {
    const s = Math.max(0, Number(seconds) || 0);
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    if (h > 0) return `${h}h ${m}m`;
    return `${m}m`;
  }

  async function refreshGatewaySummary() {
    const stat = $("stat-gateway");
    const detail = $("stat-gateway-detail");
    if (!stat || !detail) return;
    try {
      const s = await api("/api/gateway-summary");
      stat.textContent = `${s.node_count || 0} 节点 · ${formatUptime(s.uptime_seconds)}`;
      const refresh = s.background_refresh || {};
      const refreshText = refresh.enabled
        ? `后台刷新 ${refresh.interval_minutes}m，最近成功 ${refresh.last_refreshed || 0} 个`
        : "后台刷新关闭";
      detail.textContent = `库 ${s.enabled_vault_count}/${s.vault_count}，订阅 ${s.subscription_vault_count}，健康均分 ${s.health_avg_score ?? "未知"}，异常 ${s.health_degraded_count}；${refreshText}`;
    } catch (e) {
      stat.textContent = "总览不可用";
      detail.textContent = e.message || "无法读取网关状态";
    }
  }

  async function refreshHealth() {
    const text = $("health-text");
    if (!text) return;
    try {
      const h = await tryHealth();
      text.classList.toggle("is-error", !h.clash_ok);
      text.textContent = h.clash_ok
        ? `内核接口正常 (状态 ${h.clash_http_status})`
        : `内核接口异常 (状态 ${h.clash_http_status})`;
      show(text, true);
    } catch (e) {
      text.classList.add("is-error");
      text.textContent = "内核连接失败: " + (e.message || String(e));
      show(text, true);
    }
    refreshGatewaySummary().catch(() => {});
  }

  // —— 自动切换逻辑 ——
  function startAutoSwitchCheck() {
    if (autoSwitchTimer) return;
    autoSwitchTimer = setInterval(async () => {
      if (!isAutoSwitching || !currentNodeName) return;

      try {
        let info = await api("/api/proxy-delay", {
          method: "POST",
          body: JSON.stringify({ name: currentNodeName, timeout_ms: 10000 }),
        });
        
        // Hysteria2 等协议可能存在首包延迟，若失败则在 3 秒后重试一次
        if (info.error || info.delay_ms == null) {
          console.log(`[自动切换] 首次检查失败，3秒后尝试重试: ${currentNodeName}`);
          await new Promise(r => setTimeout(r, 3000));
          info = await api("/api/proxy-delay", {
            method: "POST",
            body: JSON.stringify({ name: currentNodeName, timeout_ms: 10000 }),
          });
        }

        // 如果重试后依然失败或无延迟，则尝试自动切换
        if (info.error || info.delay_ms == null) {
          console.log(`[自动切换] 检测到当前节点 ${currentNodeName} 确实不可用，准备寻找新节点...`);
          await performAutoSwitch();
        }
      } catch (e) {
        console.error("[自动切换] 健康检查请求失败", e);
      }
    }, 30000); // 每 30 秒检查一次
  }

  async function performAutoSwitch() {
    const hint = $("node-test-hint");
    if (hint) hint.textContent = "自动切换中：正在寻找可用节点...";
    
    try {
      // 1. 获取所有节点
      const summary = await loadSelectorSummary();
      const all = summary.all || [];
      if (all.length <= 1) return;

      // 2. 批量测速
      const results = [];
      const concurrency = 5;
      const queue = all.slice();
      
      const workers = Array(concurrency).fill(0).map(async () => {
        while (queue.length) {
          const name = queue.shift();
          try {
            const info = await api("/api/proxy-delay", {
              method: "POST",
              body: JSON.stringify({ name, timeout_ms: 8000 }),
            });
            if (info.delay_ms != null && !info.error) {
              results.push({ name, delay: info.delay_ms });
            }
          } catch (e) {}
        }
      });
      await Promise.all(workers);

      if (results.length === 0) {
        if (hint) hint.textContent = "自动切换失败：未找到可用节点";
        return;
      }

      // 3. 按延迟排序，选出最快的
      results.sort((a, b) => a.delay - b.delay);
      const best = results[0].name;

      if (best === currentNodeName) {
        if (hint) hint.textContent = "自动切换：当前已是最优可用节点";
        return;
      }

      console.log(`[自动切换] 找到最优节点：${best} (延迟: ${results[0].delay}ms)`);
      await switchToNode(best);
      if (hint) hint.textContent = `自动切换成功：已切换至 ${best}`;
    } catch (e) {
      if (hint) hint.textContent = "自动切换异常: " + (e.message || String(e));
    }
  }

  function initAutoSwitch() {
    const cb = $("cb-auto-switch");
    if (!cb) return;

    // 从本地存储恢复状态
    const saved = localStorage.getItem("nethub_auto_switch") === "true";
    cb.checked = saved;
    isAutoSwitching = saved;

    cb.addEventListener("change", (e) => {
      isAutoSwitching = e.target.checked;
      localStorage.setItem("nethub_auto_switch", isAutoSwitching);
      if (isAutoSwitching) {
        console.log("[自动切换] 已开启");
        startAutoSwitchCheck();
      } else {
        console.log("[自动切换] 已关闭");
        if (autoSwitchTimer) {
          clearInterval(autoSwitchTimer);
          autoSwitchTimer = null;
        }
      }
    });

    if (isAutoSwitching) startAutoSwitchCheck();
  }
  async function loadMain(meta) {
    show($("login-section"), false);
    show($("error-section"), false);
    show($("btn-logout"), true);
    try {
      const data = await loadSelectorSummary();
      fillNodeCards(data);
      await refreshHealth();
    } catch (e) {
      if (e.status === 401) {
        show($("login-section"), true);
        show($("selector-section"), false);
        show($("health-text"), false);
        show($("btn-logout"), false);
        show($("sidebar-nav"), false);
        return;
      }
      show($("selector-section"), false);
      show($("error-section"), true);
      $("error-detail").textContent =
        typeof e.data?.detail === "string"
          ? e.data.detail
          : e.data?.detail
            ? JSON.stringify(e.data.detail, null, 2)
            : e.message || String(e);
      show($("sidebar-nav"), true);
    }
  }

  async function init() {
    let meta;
    try {
      meta = await loadMeta();
      currentMeta = meta;
    } catch (e) {
      show($("error-section"), true);
      $("error-detail").textContent = "无法读取 /api/meta: " + (e.message || String(e));
      return;
    }
    show($("sidebar-nav"), true);
    const savedTab = localStorage.getItem(TAB_KEY) || "node";
    setTab(savedTab);
    if (meta.auth_configured === false) {
      show($("error-section"), true);
      $("error-detail").textContent =
        "服务器未同时配置 PANEL_ADMIN_USER 与 PANEL_ADMIN_PASSWORD，无法使用面板。请设置环境变量后重启面板。";
      return;
    }
    const logHint = $("panel-logs-hint");
    if (logHint && typeof meta.audit_log_max === "number") {
      logHint.textContent = `仅显示最近 ${meta.audit_log_max} 条日志。`;
    }
    // 并行启动非关键数据的加载，不阻塞 init 函数
    loadVaultStatus().catch(() => {});
    renderVaultManageTable().catch(() => {});
    loadMain(meta).catch(() => {});
    initAutoSwitch();

    // 优先绑定交互监听器，避免数据加载阻塞 UI 交互
    const btnReset = $("btn-vault-reset");
    if (btnReset) btnReset.addEventListener("click", resetVault);
    const btnCreate = $("btn-vault-create");
    if (btnCreate) btnCreate.addEventListener("click", createVault);

    attachPasswordToggle($("login-password"));

    const tbody = $("vault-manage-tbody");
    if (tbody) {
      tbody.addEventListener("change", async (ev) => {
        const cb = ev.target && ev.target.classList && ev.target.classList.contains("vault-enable") ? ev.target : null;
        if (!cb) return;
        const tr = cb.closest("tr");
        if (!tr) return;
        const name = tr.dataset.name;
        if (!name) return;
        try {
          await toggleVault(name, cb.checked);
        } catch (e) {
          cb.checked = !cb.checked;
          const msg = $("vault-msg");
          if (msg) {
            show(msg, true);
            msg.classList.add("err");
            msg.textContent = typeof e.data?.detail === "string" ? e.data.detail : e.message || "更新失败";
          }
        }
      });
      tbody.addEventListener("click", async (ev) => {
        const btnImp = ev.target.closest && ev.target.closest(".vault-import");
        const btnView = ev.target.closest && ev.target.closest(".vault-view");
        const btnRefresh = ev.target.closest && ev.target.closest(".vault-refresh");
        const btnRen = ev.target.closest && ev.target.closest(".vault-rename");
        const btnDel = ev.target.closest && ev.target.closest(".vault-delete");
        if (!btnImp && !btnView && !btnRefresh && !btnRen && !btnDel) return;
        const tr = ev.target.closest("tr");
        if (!tr) return;
        const name = tr.dataset.name;
        if (!name) return;

        if (btnImp) {
          showFormModal({
            title: "身份验证",
            desc: `请输入节点库“${name}”的密码以继续导入：`,
            confirmText: "确认",
            fields: [
              {
                id: "pw",
                label: "库密码",
                type: "password",
                required: true,
              }
            ]
          }, async (vals) => {
            const pw = (vals.pw || "").trim();
            if (!pw) return;
            try {
              // 在进入选择界面前，先验证密码是否正确
              await api("/api/vault/verify", {
                method: "POST",
                body: JSON.stringify({ name, password: pw }),
              });
              currentImportVault = name;
              currentImportPassword = pw;
              const targetNameEl = $("import-target-name");
              if (targetNameEl) targetNameEl.textContent = name;
              const targetNameManualEl = $("import-target-name-manual");
              if (targetNameManualEl) targetNameManualEl.textContent = name;
              showModal("modal-import-choice");
            } catch (e) {
              showConfirmModal({
                title: "验证失败",
                desc: e.data?.detail || e.message || "密码错误或无法连接服务器",
                confirmText: "知道了"
              }, () => {});
            }
          });
          return;
        }
        if (btnView) {
          await viewVaultNodes(name);
          return;
        }
        if (btnRefresh) {
          const pw = await verifyVaultPassword(name, "刷新订阅", `刷新节点库“${name}”需要验证密码：`);
          if (!pw) return;
          try {
            const r = await api("/api/vault/refresh", {
              method: "POST",
              body: JSON.stringify({ name, password: pw }),
              loading: "正在刷新订阅...",
            });
            const msg = $("vault-msg");
            if (msg) {
              show(msg, true);
              msg.classList.remove("err");
              msg.textContent = `刷新完成：写入 ${r.node_count} 条，去重 ${r.duplicate_count || 0} 条，合计 ${r.total_count} 条。`;
            }
            await loadVaultStatus();
            await renderVaultManageTable();
          } catch (e) {
            const msg = $("vault-msg");
            if (msg) {
              show(msg, true);
              msg.classList.add("err");
              msg.textContent = typeof e.data?.detail === "string" ? e.data.detail : e.message || "刷新失败";
            }
          }
          return;
        }

        const msg = $("vault-msg");
        try {
        if (btnRen) {
          await renameVault(name);
        }
        if (btnDel) {
          await deleteVault(name);
        }
          if (msg) {
            show(msg, true);
            msg.classList.remove("err");
            msg.textContent = "操作已完成";
          }
        } catch (e) {
          if (msg) {
            show(msg, true);
            msg.classList.add("err");
            msg.textContent = typeof e.data?.detail === "string" ? e.data.detail : e.message || "操作失败";
          }
        }
      });
    }

    $("btn-refresh-logs").addEventListener("click", () => loadPanelLogs());

    const btnRefreshConns = $("btn-refresh-conns");
    if (btnRefreshConns) btnRefreshConns.addEventListener("click", () => loadPanelConns(false));

    const btnCloseAllConns = $("btn-close-all-conns");
    if (btnCloseAllConns) {
      btnCloseAllConns.addEventListener("click", () => {
        showConfirmModal({
          title: "断开全部连接",
          desc: "确定要强行断开当前所有的活跃代理连接吗？这将导致正在进行的网络请求瞬间中断并重连。",
          confirmText: "确定断开",
          hideCancel: false
        }, async () => {
          try {
            await api("/api/connections", { method: "DELETE" });
            loadPanelConns(true);
          } catch (e) {
            console.error("断开所有连接失败", e);
          }
        });
      });
    }

    const connSearch = $("conn-search");
    if (connSearch) {
      connSearch.addEventListener("input", () => {
        loadPanelConns(true);
      });
    }

    $("btn-test-all").addEventListener("click", async () => {
      const btn = $("btn-test-all");
      if (!lastNodeNames.length || btn.disabled) return;
      btn.disabled = true;
      try {
        await runDelayTests(lastNodeNames);
      } finally {
        btn.disabled = false;
      }
    });

    $("node-cards").addEventListener("click", async (ev) => {
      const card = ev.target.closest(".node-card");
      if (!card) return;
      const n = card.dataset.name;
      if (ev.target.closest(".btn-icon--ping")) {
        ev.stopPropagation();
        await runDelayTests([n]);
        return;
      }
      if (isSwitching) return;
      isSwitching = true;
      try {
        await switchToNode(n);
      } finally {
        isSwitching = false;
      }
    });

    $("node-cards").addEventListener("keydown", async (ev) => {
      if (ev.key !== "Enter" && ev.key !== " ") return;
      const card = ev.target.closest(".node-card");
      if (!card || ev.target.closest(".btn-icon--ping")) return;
      ev.preventDefault();
      if (isSwitching) return;
      isSwitching = true;
      try {
        await switchToNode(card.dataset.name);
      } finally {
        isSwitching = false;
      }
    });
  }

  document.querySelectorAll(".nav-item").forEach((b) => {
    b.addEventListener("click", () => setTab(b.dataset.tab));
  });

    document.querySelectorAll(".import-choice-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        const type = btn.dataset.type;
        hideModal("modal-import-choice");
        if (type === "sub") {
          showModal("modal-import-sub");
        } else {
          const titleMap = { qr: "二维码导入", file: "文件导入", manual: "手动粘贴导入" };
          const titleEl = $("import-manual-title");
          if (titleEl) titleEl.textContent = titleMap[type] || "节点导入";
          
          showModal("modal-import-manual");
          show($("import-qr-section"), type === "qr");
          show($("import-file-section"), type === "file");
          show($("import-manual-section"), type === "manual" || type === "file" || type === "qr");
          if (type === "qr") $("btn-qr-scan").click();
        }
      });
    });

    const loginForm = $("login-form");
    if (loginForm) {
      loginForm.addEventListener("submit", async (ev) => {
        ev.preventDefault();
        const user = ($("login-username") && $("login-username").value) || "";
        const pw = $("login-password").value;
        const errEl = $("login-error");
        show(errEl, false);
        try {
          await api("/api/login", {
            method: "POST",
            body: JSON.stringify({ username: user.trim(), password: pw }),
            loading: "正在登录...",
          });
          if ($("login-username")) $("login-username").value = "";
          if ($("login-password")) $("login-password").value = "";
          const meta = await loadMeta();
          await loadMain(meta);
        } catch (e) {
          show(errEl, true);
          errEl.textContent = e.data?.detail || e.message || "登录失败";
        }
      });
    }

  const btnLogout = $("btn-logout");
  if (btnLogout) {
    btnLogout.addEventListener("click", async () => {
      try {
        await api("/api/logout", { method: "POST" });
      } catch (_) {}
      location.reload();
    });
  }

  const vaultImportForm = $("vault-import-form");
  if (vaultImportForm) {
    vaultImportForm.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const msg = $("vault-msg");
      show(msg, true);
      msg.classList.remove("err");
      msg.textContent = "正在保存…";
      const vaultName = currentImportVault || getVaultTarget();
      const pw = currentImportPassword;
      if (!pw) {
        show(msg, true);
        msg.classList.add("err");
        msg.textContent = "身份验证失效，请重新点击导入。";
        return;
      }
      const body = {
        vault_password: pw,
        urls_text: $("vault-urls").value,
        vault_name: vaultName,
      };
      try {
        const r = await api("/api/vault/import", {
          method: "POST",
          body: JSON.stringify(body),
          loading: "正在处理并重构...",
        });
        msg.textContent = `成功：写入 ${r.node_count} 条，去重 ${r.duplicate_count || 0} 条；合计 ${r.total_count} 条；配置已热重载。`;
        await loadVaultStatus();
        await renderVaultManageTable();
        hideModal("modal-import-manual");
        if ($("vault-urls")) $("vault-urls").value = "";
      } catch (e) {
        msg.classList.add("err");
        msg.textContent = typeof e.data?.detail === "string" ? e.data.detail : e.message || "导入失败";
      }
    });
  }

  const btnPreviewImport = $("btn-preview-vault-import");
  if (btnPreviewImport) {
    btnPreviewImport.addEventListener("click", async () => {
      const msg = $("vault-msg");
      const vaultName = currentImportVault || getVaultTarget();
      const pw = currentImportPassword;
      if (!pw) {
        show(msg, true);
        msg.classList.add("err");
        msg.textContent = "身份验证失效，请重新点击导入。";
        return;
      }
      try {
        const r = await api("/api/vault/preview", {
          method: "POST",
          body: JSON.stringify({
            vault_password: pw,
            urls_text: $("vault-urls").value,
            vault_name: vaultName,
          }),
          loading: "正在预览变更...",
        });
        show(msg, true);
        msg.classList.remove("err");
        msg.textContent = `预览：新增 ${r.added_count} 条，移除 ${r.removed_count} 条，保留 ${r.unchanged_count} 条，重复 ${r.duplicate_count} 条。`;
      } catch (e) {
        show(msg, true);
        msg.classList.add("err");
        msg.textContent = typeof e.data?.detail === "string" ? e.data.detail : e.message || "预览失败";
      }
    });
  }

  const btnBackupExport = $("btn-backup-export");
  if (btnBackupExport) {
    btnBackupExport.addEventListener("click", async () => {
      try {
        setGlobalLoading(true, "正在生成备份...");
        const r = await fetch("/api/backup/export", {
          method: "POST",
          credentials: "same-origin",
          headers: { "X-CSRF-Token": csrfToken },
        });
        if (!r.ok) {
          let msg = r.statusText || "导出备份失败";
          try {
            const data = await r.json();
            msg = data.detail || msg;
          } catch (_) {}
          throw new Error(msg);
        }
        const blob = await r.blob();
        const cd = r.headers.get("Content-Disposition") || "";
        const m = cd.match(/filename=([^;]+)/i);
        const filename = m ? m[1].replace(/"/g, "") : "nethub-backup.zip";
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(url);
        const msg = $("vault-msg");
        if (msg) {
          show(msg, true);
          msg.classList.remove("err");
          msg.textContent = "备份已生成。";
        }
      } catch (e) {
        const msg = $("vault-msg");
        if (msg) {
          show(msg, true);
          msg.classList.add("err");
          msg.textContent = e.message || "导出备份失败";
        }
      } finally {
        setGlobalLoading(false);
      }
    });
  }

  async function importFromSubscription() {
    const urlEl = $("vault-subscription-url");
    const url = (urlEl && urlEl.value ? urlEl.value : "").trim();
    const msg = $("vault-msg");
    if (!url) {
      show(msg, true);
      msg.classList.add("err");
      msg.textContent = "请先填写订阅地址";
      return;
    }
    show(msg, true);
    msg.classList.remove("err");
    msg.textContent = "正在拉取订阅并导入…";
    const vaultName = currentImportVault || getVaultTarget();
    const pw = currentImportPassword;
    if (!pw) {
      show(msg, true);
      msg.classList.add("err");
      msg.textContent = "身份验证失效，请重新点击导入。";
      return;
    }
    const body = { vault_password: pw, subscription_url: url, vault_name: vaultName };
    try {
      const r = await api("/api/vault/import-subscription", {
        method: "POST",
        body: JSON.stringify(body),
        loading: "正在拉取订阅并处理...",
      });
      msg.textContent = `订阅导入成功：写入 ${r.node_count} 条，去重 ${r.duplicate_count || 0} 条；合计 ${r.total_count} 条；运行配置已更新。`;
      await loadVaultStatus();
      await renderVaultManageTable();
      hideModal("modal-import-sub");
      if ($("vault-subscription-url")) $("vault-subscription-url").value = "";
    } catch (e) {
      msg.classList.add("err");
      msg.textContent = typeof e.data?.detail === "string" ? e.data.detail : e.message || "订阅导入失败";
    }
  }

  const subForm = $("vault-sub-form");
  if (subForm) subForm.addEventListener("submit", (ev) => {
    ev.preventDefault();
    importFromSubscription();
  });

  const btnClearUrls = $("btn-clear-vault-urls");
  if (btnClearUrls) {
    btnClearUrls.addEventListener("click", () => {
      const ta = $("vault-urls");
      if (ta) ta.value = "";
    });
  }

  function normalizeUrlsText(text) {
    const lines = String(text || "")
      .replace(/\r\n/g, "\n")
      .replace(/\r/g, "\n")
      .split("\n");
    const out = [];
    for (const raw of lines) {
      const line = raw.trim();
      if (!line) continue;
      if (line.startsWith("#")) continue;
      out.push(line);
    }
    return out.join("\n");
  }

  function parseCsvLike(text, delim) {
    // 轻量解析：取每行第一个单元格作为链接（兼容常见 CSV/TSV 导出）
    const lines = String(text || "")
      .replace(/\r\n/g, "\n")
      .replace(/\r/g, "\n")
      .split("\n");
    const out = [];
    for (const raw of lines) {
      const line = raw.trim();
      if (!line) continue;
      if (line.startsWith("#")) continue;
      const first = line.split(delim)[0].trim();
      if (!first) continue;
      const unquoted =
        first.length >= 2 && first[0] === '"' && first[first.length - 1] === '"'
          ? first.slice(1, -1).trim()
          : first;
      if (unquoted) out.push(unquoted);
    }
    return out.join("\n");
  }

  function appendImportedText(text, sourceLabel) {
    const ta = $("vault-urls");
    const urls = normalizeUrlsText(text);
    if (!urls) return 0;
    const current = ta ? normalizeUrlsText(ta.value) : "";
    // 去重（按行）
    const set = new Set((current ? current.split("\n") : []).filter(Boolean));
    const add = urls.split("\n").filter((ln) => ln && !set.has(ln));
    const merged = (current ? current.split("\n") : []).filter(Boolean).concat(add).join("\n");
    if (ta) ta.value = merged ? merged + "\n" : "";
    const msg = $("vault-msg");
    if (msg) {
      show(msg, true);
      msg.classList.remove("err");
      msg.textContent = `${sourceLabel}已导入：新增 ${add.length} 条`;
    }
    return add.length;
  }

  let qrStream = null;
  let qrTimer = null;

  function setQrMsg(text, isErr) {
    const el = $("qr-msg");
    if (!el) return;
    if (!text) {
      el.classList.add("hidden");
      return;
    }
    el.classList.remove("hidden");
    el.classList.toggle("error", !!isErr);
    el.textContent = text;
  }

  async function detectFromImageBitmap(detector, bitmap) {
    try {
      const codes = await detector.detect(bitmap);
      if (!codes || !codes.length) return null;
      return codes[0].rawValue || null;
    } catch {
      return null;
    }
  }

  async function stopQrScan() {
    if (qrTimer) {
      clearInterval(qrTimer);
      qrTimer = null;
    }
    const v = $("qr-video");
    if (v) v.srcObject = null;
    if (qrStream) {
      qrStream.getTracks().forEach((t) => t.stop());
      qrStream = null;
    }
    show($("qr-scan-area"), false);
    show($("btn-qr-stop"), false);
    show($("btn-qr-scan"), true);
  }

  async function startQrScan() {
    if (!("BarcodeDetector" in window)) {
      setQrMsg("当前浏览器不支持扫码导入，请使用“上传二维码图片”。", true);
      return;
    }
    const detector = new BarcodeDetector({ formats: ["qr_code"] });
    setQrMsg("正在启动摄像头…", false);
    try {
      qrStream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: "environment" },
        audio: false,
      });
    } catch (e) {
      setQrMsg("无法打开摄像头: " + (e.message || String(e)), true);
      return;
    }
    const v = $("qr-video");
    if (!v) return;
    v.srcObject = qrStream;
    try {
      await v.play();
    } catch (_) {}
    show($("qr-scan-area"), true);
    show($("btn-qr-stop"), true);
    show($("btn-qr-scan"), false);
    setQrMsg("摄像头已开启，识别成功会自动导入。", false);

    qrTimer = setInterval(async () => {
      if (!v.videoWidth || !v.videoHeight) return;
      const canvas = document.createElement("canvas");
      canvas.width = v.videoWidth;
      canvas.height = v.videoHeight;
      const ctx = canvas.getContext("2d");
      if (!ctx) return;
      ctx.drawImage(v, 0, 0, canvas.width, canvas.height);
      const bitmap = await createImageBitmap(canvas);
      const raw = await detectFromImageBitmap(detector, bitmap);
      bitmap.close && bitmap.close();
      if (!raw) return;
      const added = appendImportedText(raw, "扫码");
      if (added > 0) {
        setQrMsg(`识别成功，已导入 ${added} 条。`, false);
        await stopQrScan();
      }
    }, 450);
  }

  const btnQrScan = $("btn-qr-scan");
  if (btnQrScan) btnQrScan.addEventListener("click", startQrScan);
  const btnQrStop = $("btn-qr-stop");
  if (btnQrStop) btnQrStop.addEventListener("click", stopQrScan);

  const qrFile = $("qr-file");
  if (qrFile) {
    qrFile.addEventListener("change", async (ev) => {
      const f = ev.target && ev.target.files && ev.target.files[0];
      if (!f) return;
      try {
        if (!("BarcodeDetector" in window)) {
          setQrMsg("当前浏览器不支持二维码识别。请使用 Edge/Chrome，或改用文本/文件导入。", true);
          return;
        }
        const detector = new BarcodeDetector({ formats: ["qr_code"] });
        const bitmap = await createImageBitmap(f);
        const raw = await detectFromImageBitmap(detector, bitmap);
        bitmap.close && bitmap.close();
        if (!raw) {
          setQrMsg("未识别到二维码内容，请换一张更清晰的图片。", true);
          return;
        }
        const added = appendImportedText(raw, "二维码图片");
        setQrMsg(`识别成功，已导入 ${added} 条。`, false);
      } catch (e) {
        setQrMsg("图片识别失败: " + (e.message || String(e)), true);
      } finally {
        ev.target.value = "";
      }
    });
  }

  async function loadVaultFile(file) {
    const msg = $("vault-msg");
    if (!file) return;
    const name = (file.name || "").toLowerCase();
    const text = await file.text();
    let urls = "";
    if (name.endsWith(".csv")) urls = parseCsvLike(text, ",");
    else if (name.endsWith(".tsv")) urls = parseCsvLike(text, "\t");
    else urls = normalizeUrlsText(text);

    appendImportedText(urls, `文件“${file.name}”`);
  }

  const vaultFile = $("vault-file");
  if (vaultFile) {
    vaultFile.addEventListener("change", async (ev) => {
      const f = ev.target && ev.target.files && ev.target.files[0];
      if (!f) return;
      try {
        await loadVaultFile(f);
      } catch (e) {
        const msg = $("vault-msg");
        if (msg) {
          show(msg, true);
          msg.classList.add("err");
          msg.textContent = "文件解析失败: " + (e.message || String(e));
        }
      } finally {
        ev.target.value = "";
      }
    });
  }

  const drop = $("vault-file-drop");
  if (drop) {
    drop.addEventListener("keydown", (ev) => {
      if (ev.key !== "Enter" && ev.key !== " ") return;
      ev.preventDefault();
      vaultFile && vaultFile.click();
    });
    drop.addEventListener("dragover", (ev) => {
      ev.preventDefault();
      drop.classList.add("is-dragover");
    });
    drop.addEventListener("dragleave", () => drop.classList.remove("is-dragover"));
    drop.addEventListener("drop", async (ev) => {
      ev.preventDefault();
      drop.classList.remove("is-dragover");
      const f = ev.dataTransfer && ev.dataTransfer.files && ev.dataTransfer.files[0];
      if (!f) return;
      try {
        await loadVaultFile(f);
      } catch (e) {
        const msg = $("vault-msg");
        if (msg) {
          show(msg, true);
          msg.classList.add("err");
          msg.textContent = "文件解析失败: " + (e.message || String(e));
        }
      }
    });
  }

  document.querySelectorAll(".btn-export-sub-trigger").forEach((btn) => {
    btn.onclick = () => {
       showModal("modal-export-sub");
    };
  });

  const showExportMsg = (text, isErr) => {
    const el = $("export-msg");
    if (!el) return;
    el.classList.remove("hidden");
    el.classList.toggle("err", !!isErr);
    el.textContent = text;
    setTimeout(() => {
      el.classList.add("hidden");
    }, 3000);
  };

  const setupExportCopy = (btnId, apiPath) => {
    const btn = $(btnId);
    if (btn) {
      btn.onclick = async () => {
        let url = window.location.origin + apiPath;
        if (subToken) {
          url += "?token=" + subToken;
        }
        try {
          await navigator.clipboard.writeText(url);
          showExportMsg("订阅链接已复制到剪贴板！", false);
        } catch (e) {
          showExportMsg("复制失败，请手动选择复制：" + url, true);
        }
      };
    }
  };

  const setupExportDl = (btnId, apiPath) => {
    const btn = $(btnId);
    if (btn) {
      btn.onclick = () => {
        let url = apiPath;
        if (subToken) {
          url += "?token=" + subToken;
        }
        window.open(url, "_blank");
      };
    }
  };

  setupExportDl("btn-export-clash-dl", "/api/export/clash");
  setupExportCopy("btn-export-clash-copy", "/api/export/clash");
  
  setupExportDl("btn-export-v2ray-dl", "/api/export/v2ray");
  setupExportCopy("btn-export-v2ray-copy", "/api/export/v2ray");
  
  setupExportDl("btn-export-singbox-dl", "/api/export/singbox");
  setupExportCopy("btn-export-singbox-copy", "/api/export/singbox");

  const nodeSearch = $("node-search");
  if (nodeSearch) {
    nodeSearch.addEventListener("input", () => {
      if (currentSelectorData) fillNodeCards(currentSelectorData);
    });
  }

  const nodeSort = $("node-sort");
  if (nodeSort) {
    nodeSort.addEventListener("change", () => {
      if (currentSelectorData) fillNodeCards(currentSelectorData);
    });
  }

  init();
})();
