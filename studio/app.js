/* Studio UI: live polling, roster, activity feed, chat, dashboard, reports. */
(() => {
  "use strict";
  const $ = (s, el = document) => el.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const money = (v, d = 2) => v == null ? "—" : (v < 0 ? "−$" : "$") + Math.abs(v).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });
  const pct = (v, d = 2) => v == null ? "—" : (v >= 0 ? "+" : "−") + Math.abs(v * 100).toFixed(d) + "%";
  const dcls = (v) => v == null || Math.abs(v) < 1e-9 ? "" : v > 0 ? "delta-up" : "delta-down";
  const hhmm = (iso) => { try { return new Date(iso).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }); } catch { return ""; } };
  const dt = (iso) => { try { return new Date(iso).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }); } catch { return iso || ""; } };
  const DESK_COLOR = { day: "var(--series-day)", swing: "var(--series-swing)", long: "var(--series-long)", crypto: "#e66767" };
  const DESK_HEX = { day: "#3987e5", swing: "#d95926", long: "#199e70", crypto: "#e66767" };
  const api = async (path, opts) => { const r = await fetch(path, opts); if (!r.ok) throw new Error(r.status); const ct = r.headers.get("content-type") || ""; return ct.includes("json") ? r.json() : r.text(); };

  let STATE = null, view = "studio", chatId = null, dashTimer = null, eqRange = "1M", showTable = false;

  // ------------------------------------------------------------------ tabs
  function setView(v) {
    view = v;
    document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.view === v));
    document.querySelectorAll(".view").forEach((s) => s.classList.toggle("active", s.id === `view-${v}`));
    clearInterval(dashTimer);
    if (v === "dashboard") { loadDashboard(); dashTimer = setInterval(loadDashboard, 30000); }
    if (v === "reports") loadReports();
  }
  document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => setView(t.dataset.view)));

  // ------------------------------------------------------------------ live state
  async function poll() {
    try {
      const st = await api("/api/state"); STATE = st;
      World.setState(st); renderTop(st); renderRoster(st); renderFeed(st);
      if (chatId) { const a = st.agents.find((x) => x.id === chatId); if (a) $("#chat-act").textContent = "● " + a.activity; }
    } catch (e) { $("#market").textContent = "offline"; $("#market").className = "pill closed"; }
  }
  function renderTop(st) {
    const ct = $("#crypto-toggle");
    ct.textContent = `₿ Crypto ${st.crypto_enabled ? "ON" : "OFF"}`; ct.className = "pill toggle " + (st.crypto_enabled ? "on" : "off");
    ct.dataset.on = st.crypto_enabled ? "1" : "";
    $("#paper").style.display = st.paper ? "" : "none";
    $("#paper").textContent = st.paper ? `PAPER · ${String(st.profile || "").toUpperCase()}` : "LIVE";
    $("#market").textContent = st.market.open ? "Market open" : "Market closed";
    $("#market").className = "pill " + (st.market.open ? "open" : "closed");
    $("#clock").textContent = st.now_et;
    $("#equity").textContent = money(st.account.equity);
    $("#daychg").textContent = st.account.day_change == null ? "" : pct(st.account.day_change) + " today";
  }
  const nameOf = (id) => STATE?.agents.find((a) => a.id === id)?.name || id;
  function renderRoster(st) {
    $("#roster-list").innerHTML = st.agents.map((a) => `
      <li data-id="${a.id}" title="${esc(a.activity)}">
        <span class="dot" style="background:${a.color}">${a.badge}</span>
        <span class="meta"><b>${esc(a.name)}</b>${a.busy ? '<span class="busy-dot" title="working"></span>' : ""}
          <small>${esc(a.role)}</small><small class="act">${esc(a.activity)}</small></span>
      </li>`).join("");
    document.querySelectorAll("#roster-list li").forEach((li) => li.addEventListener("click", () => openChat(li.dataset.id)));
  }
  function renderFeed(st) {
    $("#feed-list").innerHTML = st.events.map((e) => `<li><span class="t">${hhmm(e.ts)}</span><b>${esc(nameOf(e.agent))}</b> ${esc(e.msg)}</li>`).join("")
      || '<li class="muted">Quiet so far…</li>';
  }

  // ------------------------------------------------------------------ hover tooltip
  World.onHover = (h) => {
    const tip = $("#tooltip");
    if (!h) { tip.style.display = "none"; return; }
    const a = STATE?.agents.find((x) => x.id === h.bot.id); if (!a) return;
    tip.innerHTML = `<b>${a.badge} ${esc(a.name)}</b><small>${esc(a.role)}</small><p>${a.busy ? "🟢 " : "🟡 "}${esc(a.activity)}</p><small>Click to chat</small>`;
    tip.style.display = "block"; tip.style.left = Math.min(h.x + 16, innerWidth - 300) + "px"; tip.style.top = (h.y + 10) + "px";
  };

  // ------------------------------------------------------------------ chat
  const CHIPS = {
    researcher: ["What are you working on?", "Any stocks you like right now?", "What's the news saying?"],
    trader: ["What are you holding?", "Why did you pick your last trade?", "How's your desk doing?"],
    challenger: ["What did you argue about lately?", "How good have your calls been?", "What worries you right now?"],
    manager: ["How are we doing overall?", "Summarize the latest report", "Which desk is doing best?"],
    lab: ["How do the brains evolve?", "How good is each champion?", "How do you avoid overfitting?"],
  };
  async function openChat(id) {
    const a = STATE?.agents.find((x) => x.id === id); if (!a) return;
    if (view !== "studio") setView("studio");
    chatId = id; World.setChatting(id); World.focus(id);
    $("#chat").classList.add("open"); $("#chat").setAttribute("aria-hidden", "false");
    const av = $("#chat-avatar"); av.style.background = a.color; av.textContent = a.badge;
    $("#chat-name").textContent = a.name; $("#chat-role").textContent = a.role; $("#chat-act").textContent = "● " + a.activity;
    $("#chat-chips").innerHTML = (CHIPS[a.kind] || []).map((c) => `<button class="chip">${esc(c)}</button>`).join("");
    document.querySelectorAll(".chip").forEach((c) => c.addEventListener("click", () => send(c.textContent)));
    const msgs = $("#chat-msgs"); msgs.innerHTML = "";
    try {
      const hist = await api(`/api/chat/history?agent=${id}`);
      if (!hist.length) addMsg("bot", `Hi! I'm ${a.name}, the ${a.role.toLowerCase()}. ${a.bio} Ask me anything.`);
      hist.forEach((h) => addMsg(h.role, h.text));
    } catch { addMsg("bot", "I can't reach the studio server right now."); }
    $("#chat-input").focus();
  }
  function closeChat() { chatId = null; World.setChatting(null); $("#chat").classList.remove("open"); $("#chat").setAttribute("aria-hidden", "true"); }
  function addMsg(role, text, model) {
    const d = document.createElement("div"); d.className = `msg ${role === "user" ? "user" : "bot"}`;
    if (role === "user") d.textContent = text; else d.innerHTML = md(text);
    if (model) { const s = document.createElement("small"); s.textContent = model; d.appendChild(s); }
    $("#chat-msgs").appendChild(d); $("#chat-msgs").scrollTop = 1e9; return d;
  }
  async function send(text) {
    text = (text || "").trim(); if (!text || !chatId) return;
    const id = chatId; addMsg("user", text); $("#chat-input").value = "";
    const typing = addMsg("bot", `${nameOf(id)} is thinking…`); typing.classList.add("typing");
    try {
      const r = await api("/api/chat", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ agent: id, message: text }) });
      typing.remove(); if (chatId === id) addMsg("bot", r.text, r.model);
      World.say(id, r.text.length > 70 ? r.text.slice(0, 68) + "…" : r.text);
    } catch { typing.textContent = "Couldn't reach the bot - is the studio running?"; }
  }
  $("#chat-form").addEventListener("submit", (e) => { e.preventDefault(); send($("#chat-input").value); });
  $("#chat-close").addEventListener("click", closeChat);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && chatId) closeChat(); });

  // ------------------------------------------------------------------ charts
  function lineChart(holder, series, { height = 260, fmtY = (v) => pct(v, 1), fmtX = (t) => t, markers = false } = {}) {
    holder.innerHTML = `<div class="legend">${series.length > 1 ? series.map((s) => `<span><i style="background:${s.color}"></i>${esc(s.name)}</span>`).join("") : ""}</div>
      <div class="chart"><canvas></canvas><div class="tip"></div></div>`;
    const cv = $("canvas", holder), tip = $(".tip", holder), dpr = Math.min(2, devicePixelRatio || 1);
    const W = holder.clientWidth, H = height, padL = 52, padR = 110, padT = 10, padB = 26;
    cv.width = W * dpr; cv.height = H * dpr; cv.style.height = H + "px";
    const c = cv.getContext("2d"); c.scale(dpr, dpr);
    const n = Math.max(...series.map((s) => s.values.length));
    if (!n) { c.fillStyle = "#9a8a78"; c.font = "13px Nunito"; c.fillText("Not enough data yet", padL, H / 2); return; }
    const vals = series.flatMap((s) => s.values.map((p) => p.v).filter((v) => v != null));
    let lo = Math.min(0, ...vals), hi = Math.max(0, ...vals); if (hi - lo < 1e-6) { hi += .01; lo -= .01; }
    const pad = (hi - lo) * .08; lo -= pad; hi += pad;
    const X = (i) => padL + (n === 1 ? (W - padL - padR) / 2 : i * (W - padL - padR) / (n - 1)), Y = (v) => padT + (1 - (v - lo) / (hi - lo)) * (H - padT - padB);
    c.font = "11px Nunito"; c.textBaseline = "middle";
    for (let k = 0; k <= 4; k++) {
      const v = lo + (hi - lo) * k / 4, y = Y(v);
      c.strokeStyle = "rgba(255,255,255,.06)"; c.lineWidth = 1; c.beginPath(); c.moveTo(padL, y); c.lineTo(W - padR, y); c.stroke();
      c.fillStyle = "#9a8a78"; c.textAlign = "right"; c.fillText(fmtY(v), padL - 8, y);
    }
    if (lo < 0 && hi > 0) { c.strokeStyle = "rgba(255,255,255,.18)"; c.beginPath(); c.moveTo(padL, Y(0)); c.lineTo(W - padR, Y(0)); c.stroke(); }
    const xs = series[0].values; c.textAlign = "center"; c.fillStyle = "#9a8a78";
    for (let k = 0; k < 5 && xs.length; k++) { const i = Math.round(k * (xs.length - 1) / 4); c.fillText(fmtX(xs[i].t), X(i), H - 10); }
    for (const s of series) {
      c.strokeStyle = s.color; c.lineWidth = 2; c.lineJoin = "round"; c.beginPath(); let started = false;
      s.values.forEach((p, i) => { if (p.v == null) { started = false; return; } started ? c.lineTo(X(i), Y(p.v)) : c.moveTo(X(i), Y(p.v)); started = true; });
      c.stroke();
      if (markers) s.values.forEach((p, i) => { if (p.v == null) return; c.beginPath(); c.arc(X(i), Y(p.v), 4, 0, 7); c.fillStyle = p.filled ? s.color : "#2b2420"; c.fill(); c.strokeStyle = "#2b2420"; c.lineWidth = 2; c.stroke(); c.strokeStyle = s.color; c.lineWidth = 1.5; c.beginPath(); c.arc(X(i), Y(p.v), 4, 0, 7); c.stroke(); });
    }
    // direct labels at the line ends (nudged apart)
    const ends = series.map((s) => { for (let i = s.values.length - 1; i >= 0; i--) if (s.values[i].v != null) return { s, i, y: Y(s.values[i].v), v: s.values[i].v }; return null; }).filter(Boolean).sort((a, b) => a.y - b.y);
    for (let k = 1; k < ends.length; k++) if (ends[k].y - ends[k - 1].y < 14) ends[k].y = ends[k - 1].y + 14;
    c.textAlign = "left";
    for (const e of ends) { c.fillStyle = e.s.color; c.beginPath(); c.arc(X(e.i), Y(e.v), 3.5, 0, 7); c.fill(); c.fillStyle = "#f4ead9"; c.font = "700 11px Nunito"; c.fillText(`${e.s.short || e.s.name} ${fmtY(e.v)}`, W - padR + 8, e.y); }
    // crosshair + tooltip
    const base = c.getImageData(0, 0, cv.width, cv.height);
    cv.addEventListener("mousemove", (ev) => {
      const r = cv.getBoundingClientRect(), mx = ev.clientX - r.left;
      const i = Math.max(0, Math.min(n - 1, Math.round((mx - padL) / ((W - padL - padR) / Math.max(1, n - 1)))));
      c.putImageData(base, 0, 0);
      c.save(); c.setTransform(dpr, 0, 0, dpr, 0, 0);
      c.strokeStyle = "rgba(244,234,217,.35)"; c.lineWidth = 1; c.beginPath(); c.moveTo(X(i), padT); c.lineTo(X(i), H - padB); c.stroke();
      for (const s of series) { const p = s.values[i]; if (p?.v == null) continue; c.fillStyle = s.color; c.strokeStyle = "#2b2420"; c.lineWidth = 2; c.beginPath(); c.arc(X(i), Y(p.v), 4.5, 0, 7); c.fill(); c.stroke(); }
      c.restore();
      const row = series[0].values[i];
      tip.innerHTML = `<b>${esc(fmtX(row.t, true))}</b><br>` + series.map((s) => `<span style="color:${s.color}">●</span> ${esc(s.name)}: <b>${s.values[i]?.v == null ? "—" : fmtY(s.values[i].v)}</b>${s.values[i]?.extra ? " · " + esc(s.values[i].extra) : ""}`).join("<br>");
      tip.style.display = "block"; tip.style.left = Math.min(X(i) + 12, W - 220) + "px"; tip.style.top = "10px";
    });
    cv.addEventListener("mouseleave", () => { c.putImageData(base, 0, 0); tip.style.display = "none"; });
  }

  // ------------------------------------------------------------------ dashboard
  async function loadDashboard() {
    let d;
    try { d = await api("/api/dashboard"); } catch { $("#dash").innerHTML = '<div class="card span-12">Dashboard unavailable - is the studio running?</div>'; return; }
    const a = d.account || {}, st = d.start || {}, eq = a.equity;
    const allTrades = d.desks.reduce((s, x) => s + x.trades, 0), wins = d.desks.reduce((s, x) => s + x.wins, 0);
    const since = st.equity && eq ? eq / st.equity - 1 : null, day = a.last_equity && eq ? eq / a.last_equity - 1 : null;
    const lastSpy = d.spy_now || STATE?.spy, spySince = st.spy && lastSpy ? lastSpy / st.spy - 1 : null;
    const dec = {}; for (const r of d.decisions) (dec[r.desk] ||= {})[r.action] = r;
    $("#dash").innerHTML = `
      <div class="card kpi hero span-3"><small>Equity (paper)</small><b>${money(eq)}</b><span>Cash ${money(a.cash)}</span></div>
      <div class="card kpi span-3"><small>Today</small><b class="${dcls(day)}">${pct(day)}</b><span>${eq && a.last_equity ? money(eq - a.last_equity) : "—"} vs yesterday's close</span></div>
      <div class="card kpi span-3"><small>Since the studio opened</small><b class="${dcls(since)}">${pct(since)}</b><span>S&amp;P 500 over the same time: ${pct(spySince)}</span></div>
      <div class="card kpi span-3"><small>Closed trades</small><b>${allTrades}</b><span>${allTrades ? `${wins} wins · ${Math.round(100 * wins / allTrades)}% win rate` : "no closed trades yet"}</span></div>

      <div class="card span-8"><h3>Portfolio vs S&amp;P 500 <small>% change over the period</small>
        <span class="seg" id="rng">${["1D", "1W", "1M", "3M", "1A"].map((r) => `<button class="${r === eqRange ? "on" : ""}" data-r="${r}">${r === "1A" ? "1Y" : r}</button>`).join("")}</span></h3>
        <div id="eqchart"></div><button class="linkbtn" id="eqtable-t">${showTable ? "Hide" : "Show"} data table</button><div id="eqtable"></div></div>
      <div class="card span-4"><h3>AI brains <small>today's usage</small></h3>
        <table><tr><th>Model</th><th class="r">Requests</th><th class="r">Tokens</th></tr>
        ${d.llm.map((u) => `<tr><td>${esc(u.provider)}:${esc(u.model)}</td><td class="r">${u.requests}</td><td class="r">${(u.tokens || 0).toLocaleString()}</td></tr>`).join("") || '<tr><td colspan=3 class="empty">No AI calls yet today</td></tr>'}</table>
        <p class="muted" style="font-size:12px;margin:10px 0 4px">Available now: ${esc((d.llm_available.smart || []).join(", ") || "none")}</p>
        <h3 style="margin-top:14px">Market backdrop</h3><p style="margin:0;color:var(--text-secondary);font-size:13px">${esc(d.market.summary || "—")} ${d.market.risk ? `<br><small class="muted">Risk ${d.market.risk}/5</small>` : ""}</p></div>

      ${d.desks.map((x) => {
        const used = x.capital ? Math.min(1, x.invested / x.capital) : 0, b = x.backtest || {};
        return `<div class="card desk-card span-3"><h3><span class="tag"><i style="background:${DESK_COLOR[x.id]}"></i>${esc(x.title)}</span><span class="status-pill ${x.brain}">${x.id === "crypto" && x.brain === "probation" ? "sitting out" : x.brain}</span></h3>
          <div class="muted" style="font-size:12px">Invested ${money(x.invested, 0)} of ${money(x.capital, 0)} (${Math.round(x.sleeve * 100)}% of equity${x.size_mult !== 1 ? `, size ×${x.size_mult}` : ""})</div>
          <div class="bar"><i style="width:${used * 100}%;background:${DESK_COLOR[x.id]}"></i></div>
          <dl><dt>Open positions</dt><dd>${x.positions} / ${x.max_positions}</dd>
          <dt>Closed trades · win rate</dt><dd>${x.trades} · ${x.trades ? Math.round(100 * x.wins / x.trades) + "%" : "—"}</dd>
          <dt>Realized P&amp;L</dt><dd>${money(x.realized)}</dd><dt>Unrealized P&amp;L</dt><dd>${money(x.unrealized)}</dd>
          <dt>Backtest on unseen data</dt><dd>${b.trades ? `${pct(b.total, 1)} · ${b.trades} trades · ${Math.round(100 * (b.winrate || 0))}% wins` : "—"}</dd>
          <dt>AI veto · Challenger</dt><dd>${esc(x.llm_mode)} · ${esc(x.challenger_mode)}</dd></dl>
          ${x.id === "crypto" && d.crypto_stage ? `<div class="muted" style="font-size:12px;margin-top:8px;line-height:1.4"><b style="color:var(--text-secondary)">Stage:</b> ${esc(d.crypto_stage.label)}</div>` : ""}</div>`;
      }).join("")}

      <div class="card span-7"><h3>Open positions <small>${d.positions.length} held</small></h3><div class="scroll"><table>
        <tr><th>Desk</th><th>Symbol</th><th class="r">Qty</th><th class="r">Entry</th><th class="r">Now</th><th class="r">P&amp;L</th><th class="r">Stop</th><th class="r">Target</th><th>Exit by</th></tr>
        ${d.positions.map((p) => `<tr><td><span class="tag"><i style="background:${DESK_COLOR[p.desk]}"></i>${p.desk}</span></td><td><b>${esc(p.symbol)}</b></td>
          <td class="r">${(+p.qty).toLocaleString(undefined, { maximumFractionDigits: 3 })}</td><td class="r">${p.entry ? p.entry.toFixed(2) : "—"}</td><td class="r">${p.price ? p.price.toFixed(2) : "—"}</td>
          <td class="r"><span class="${dcls(p.uplpc)}">${pct(p.uplpc)}</span></td><td class="r">${p.stop ?? "—"}</td><td class="r">${p.target ?? "—"}</td>
          <td>${p.deadline ? dt(p.deadline) : p.legacy ? '<span class="muted">from old bot</span>' : "—"}</td></tr>`).join("") || '<tr><td colspan=9 class="empty">No positions</td></tr>'}
      </table></div></div>
      <div class="card span-5"><h3>Challenger debates <small>${esc(d.challenger.text || "")}</small></h3><div class="scroll">
        ${d.debates.map((x) => `<div class="debate"><div><span class="tag"><i style="background:${DESK_COLOR[x.desk]}"></i>${esc(x.symbol)}</span>
          <span class="v ${x.verdict}">${x.verdict}</span> <small class="muted">${dt(x.ts)}${x.mode !== "veto" ? " · advisory" : ""}${x.later_ret != null ? ` · moved ${pct(x.later_ret)} after` : ""}</small></div>
          <div style="font-size:12.5px;margin-top:4px">${esc(x.reasoning)}</div>
          <div class="pc"><div><b>Pros</b><ul>${x.pros.map((p) => `<li>${esc(p)}</li>`).join("")}</ul></div><div><b>Cons</b><ul>${x.cons.map((p) => `<li>${esc(p)}</li>`).join("")}</ul></div></div></div>`).join("")
          || '<div class="empty">No trades debated yet. Devi debates every trade before it is placed.</div>'}
      </div></div>

      <div class="card span-7"><h3>Recent closed trades</h3><div class="scroll"><table>
        <tr><th>Desk</th><th>Symbol</th><th>Closed</th><th class="r">P&amp;L</th><th class="r">$</th><th>Grade</th><th>Exit</th></tr>
        ${d.trades.map((t) => `<tr title="${esc(t.lesson || "")}"><td><span class="tag"><i style="background:${DESK_COLOR[t.desk]}"></i>${t.desk}</span></td><td><b>${esc(t.symbol)}</b></td><td>${dt(t.exit_time)}</td>
          <td class="r"><span class="${dcls(t.pnl_pct)}">${pct(t.pnl_pct)}</span></td><td class="r">${money(t.pnl)}</td><td>${t.grade || "…"}</td><td>${esc(t.exit_reason || "")}</td></tr>`).join("") || '<tr><td colspan=7 class="empty">No closed trades yet</td></tr>'}
      </table></div></div>
      <div class="card span-5"><h3>Neuroevolution <small>best score on unseen data, per training run</small></h3><div id="evochart"></div>
        <p class="muted" style="font-size:12px;margin:6px 0 0">Filled dot = a new champion brain was promoted. Higher = better risk-adjusted results on data the brains never trained on.</p></div>

      <div class="card span-6"><h3>Lessons the bots learned</h3><div class="scroll">
        ${d.lessons.map((l) => `<div class="debate" style="font-size:12.5px"><span class="tag"><i style="background:${DESK_COLOR[l.desk]}"></i>${l.desk}</span> <b>${l.grade}</b> <small class="muted">${dt(l.ts)}</small><div>${esc(l.text)}</div></div>`).join("") || '<div class="empty">Lessons appear after the first trades close.</div>'}</div></div>
      <div class="card span-6"><h3>Are the AI filters helping? <small>avg price move after the decision</small></h3><table>
        <tr><th>Desk</th><th class="r">Bought</th><th class="r">PM vetoed</th><th class="r">Challenger rejected</th></tr>
        ${["day", "swing", "long", "crypto"].map((k) => { const x = dec[k] || {}; const f = (r) => r ? `${r.n} · ${r.a == null ? "pending" : pct(r.a)}` : "0"; return `<tr><td><span class="tag"><i style="background:${DESK_COLOR[k]}"></i>${k}</span></td><td class="r">${f(x.buy)}</td><td class="r">${f(x.veto)}</td><td class="r">${f(x.challenged)}</td></tr>`; }).join("")}
      </table><p class="muted" style="font-size:12px">If rejected stocks keep beating bought ones, the manager automatically takes the AI's veto power away.</p></div>`;

    document.querySelectorAll("#rng button").forEach((b) => b.addEventListener("click", () => { eqRange = b.dataset.r; document.querySelectorAll("#rng button").forEach((x) => x.classList.toggle("on", x === b)); drawEquity(); }));
    $("#eqtable-t").addEventListener("click", () => { showTable = !showTable; $("#eqtable-t").textContent = (showTable ? "Hide" : "Show") + " data table"; drawEquity(); });
    drawEquity();
    const evo = d.evolution;
    lineChart($("#evochart"), ["day", "swing", "long", "crypto"].map((k) => ({
      name: k[0].toUpperCase() + k.slice(1), short: k, color: DESK_HEX[k],
      values: evo.map((e) => e.desk === k ? { t: e.ts, v: e.best_holdout, filled: !!e.promoted, extra: e.promoted ? "promoted" : "kept champion" } : { t: e.ts, v: null }),
    })).filter((s) => s.values.some((p) => p.v != null)), { height: 220, fmtY: (v) => v.toFixed(1), fmtX: (t, long) => long ? dt(t) : new Date(t).toLocaleDateString([], { month: "short", day: "numeric" }), markers: true });
  }
  async function drawEquity() {
    const holder = $("#eqchart"); if (!holder) return;
    let pts = [];
    try { pts = await api(`/api/equity?range=${eqRange}`); } catch {}
    const intraday = eqRange === "1D" || eqRange === "1W";
    const fx = (t, long) => { const d = new Date(t); return long || intraday ? d.toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) : d.toLocaleDateString([], { month: "short", day: "numeric" }); };
    lineChart(holder, [
      { name: "Portfolio", short: "Portfolio", color: "#e8b45a", values: pts.map((p) => ({ t: p.t, v: p.eq_pct, extra: money(p.equity) })) },
      { name: "S&P 500 (SPY)", short: "S&P 500", color: "#3987e5", values: pts.map((p) => ({ t: p.t, v: p.spy_pct })) },
    ], { height: 270, fmtX: fx });
    $("#eqtable").innerHTML = showTable ? `<div class="scroll" style="max-height:200px;margin-top:8px"><table><tr><th>Time</th><th class="r">Equity</th><th class="r">Portfolio</th><th class="r">S&amp;P 500</th></tr>
      ${pts.slice(-60).reverse().map((p) => `<tr><td>${fx(p.t, true)}</td><td class="r">${money(p.equity)}</td><td class="r">${pct(p.eq_pct)}</td><td class="r">${pct(p.spy_pct)}</td></tr>`).join("")}</table></div>` : "";
  }

  // ------------------------------------------------------------------ reports
  function md(src) {
    const inline = (s) => esc(s).replace(/`([^`]+)`/g, "<code>$1</code>").replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>").replace(/(^|[\s(])_([^_]+)_(?=[\s).,:]|$)/g, "$1<em>$2</em>");
    const lines = src.split(/\r?\n/), out = []; let i = 0, inList = false;
    const closeList = () => { if (inList) { out.push("</ul>"); inList = false; } };
    while (i < lines.length) {
      const l = lines[i];
      if (/^\s*\|/.test(l)) {
        closeList(); const rows = [];
        while (i < lines.length && /^\s*\|/.test(lines[i])) rows.push(lines[i++]);
        const cells = (r) => r.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
        const body = rows.filter((r, k) => k !== 1 || !/^\s*\|[\s:|-]+\|?\s*$/.test(r));
        out.push("<table><tr>" + cells(body[0]).map((c) => `<th>${inline(c)}</th>`).join("") + "</tr>" +
          body.slice(1).map((r) => "<tr>" + cells(r).map((c) => `<td>${inline(c)}</td>`).join("") + "</tr>").join("") + "</table>");
        continue;
      }
      const h = l.match(/^(#{1,4})\s+(.*)/);
      if (h) { closeList(); out.push(`<h${h[1].length}>${inline(h[2])}</h${h[1].length}>`); }
      else if (/^\s*-\s+/.test(l)) { if (!inList) { out.push("<ul>"); inList = true; } const ind = l.match(/^\s*/)[0].length; out.push(`<li style="margin-left:${ind * 8}px">${inline(l.replace(/^\s*-\s+/, ""))}</li>`); }
      else if (/^---\s*$/.test(l)) { closeList(); out.push("<hr>"); }
      else if (!l.trim()) closeList();
      else { closeList(); out.push(`<p>${inline(l)}</p>`); }
      i++;
    }
    closeList(); return out.join("\n");
  }
  async function loadReports(select) {
    let list = [];
    try { list = await api("/api/reports"); } catch {}
    $("#rep-items").innerHTML = list.map((r, k) => `<li data-n="${esc(r.name)}">${esc(r.title)}${k === 0 ? ' <small class="muted">· latest</small>' : ""}</li>`).join("") || '<li class="muted">No reports yet</li>';
    document.querySelectorAll("#rep-items li[data-n]").forEach((li) => li.addEventListener("click", () => openReport(li.dataset.n)));
    const first = select || list[0]?.name; if (first) openReport(first);
  }
  async function openReport(name) {
    document.querySelectorAll("#rep-items li").forEach((li) => li.classList.toggle("on", li.dataset.n === name));
    try { $("#rep-body").innerHTML = md(await api(`/api/report?name=${encodeURIComponent(name)}`)); $("#rep-body").scrollTop = 0; }
    catch { $("#rep-body").innerHTML = '<p class="muted">Could not load that report.</p>'; }
  }
  $("#rep-new").addEventListener("click", async () => {
    const b = $("#rep-new"); b.disabled = true; b.textContent = "Morgan is writing…";
    try { const r = await api("/api/report/new", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }); await loadReports(r.name); }
    catch {} b.disabled = false; b.textContent = "Write one now";
  });

  document.querySelectorAll(".roster .card-h, .feed .card-h").forEach((h) => h.addEventListener("click", () => h.parentElement.classList.toggle("collapsed")));

  $("#crypto-toggle").addEventListener("click", async () => {
    const turnOn = !$("#crypto-toggle").dataset.on;
    if (!turnOn && !confirm("Switch the crypto desk OFF? It stops opening new crypto trades (any open ones are still managed until they close).")) return;
    try { await api("/api/crypto", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ enabled: turnOn }) }); } catch {}
    poll();
  });

  // ------------------------------------------------------------------ boot
  World.init($("#world"), { onBot: openChat, onBoard: () => setView("dashboard"), onShelf: () => setView("reports") }).then(() => {
    poll(); setInterval(poll, 4000);
    const h = location.hash.slice(1); if (["dashboard", "reports"].includes(h)) setView(h);
    if (h.startsWith("chat=")) setTimeout(() => openChat(h.slice(5)), 300);
  });
})();
