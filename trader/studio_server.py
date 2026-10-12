"""The Studio: a local web app (http://localhost:8765) showing the bots at
work, a chat with each bot, a live dashboard and the reports. Runs inside the
same 24/7 process as the agents. Only reachable from this PC."""
import json
import mimetypes
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pandas as pd

from . import brain, config, db, llm, log, risk
from .personas import AGENTS
from .timeutil import ET, now_et

L = log.get("studio")
STATIC = config.ROOT / "studio"
PORT = config._i("STUDIO_PORT", 8765)
HOST = config._s("STUDIO_HOST", "127.0.0.1")          # 0.0.0.0 only behind a private network (e.g. Tailscale)
PASSWORD = config._s("STUDIO_PASSWORD")               # set this whenever the Studio is reachable from the internet
IDLE_PREFIXES = ("crypto desk switched off", "crypto desk is switched off", "watching", "market closed", "waiting", "sitting out", "no fresh", "brain found no")

_cache = {}
_cache_lock = threading.Lock()
_broker = None


def broker():
    global _broker
    if _broker is None:
        from .broker import Broker
        _broker = Broker()
    return _broker


def cached(key, ttl, fn):
    with _cache_lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
    try:
        val = fn()
    except Exception as e:
        L.warning("studio data %s failed: %s", key, e)
        return hit[1] if hit else None
    with _cache_lock:
        _cache[key] = (time.time(), val)
    return val


def _age_s(iso):
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(iso)).total_seconds()
    except Exception:
        return 1e9


# ---------------------------------------------------------------- state (polled every few seconds)
def state():
    agents = []
    for aid, p in AGENTS.items():
        act = db.kv_get(f"activity:{aid}") or {}
        hb = db.kv_get(f"heartbeat:{aid}") or {}
        text = act.get("text", "")
        busy = _age_s(act.get("at", "")) < 300 and not text.lower().startswith(IDLE_PREFIXES)
        agents.append({"id": aid, **p, "activity": text or "Getting settled in", "activity_at": act.get("at"),
                       "heartbeat_at": hb.get("at"), "status": hb.get("status", "starting"), "busy": busy,
                       "alive": _age_s(hb.get("at", "")) < 3600})
    acct = cached("account", 20, lambda: broker().account()) or {}
    clock = cached("clock", 60, lambda: broker().clock()) or {}
    signals = {
        "handoff": {d: db.kv_get(f"handoff:{d}") for d in config.DESKS},
        "buy": db.one("SELECT id, agent, msg, ts FROM events WHERE msg LIKE 'BUY %' ORDER BY id DESC LIMIT 1"),
        "debate": db.one("SELECT id, desk, symbol, verdict, ts FROM debates ORDER BY id DESC LIMIT 1"),
        "evolution": db.one("SELECT id, desk, promoted, ts FROM evolution_log ORDER BY id DESC LIMIT 1"),
        "review": db.one("SELECT id, desk, symbol, grade, pnl_pct FROM positions WHERE reviewed=1 "
                         "ORDER BY exit_time DESC LIMIT 1"),
        "report": db.kv_get("last_report_at"),
    }
    events = db.query("SELECT id, ts, agent, level, msg FROM events WHERE msg NOT LIKE '%started' "
                      "ORDER BY id DESC LIMIT 14")
    start = db.kv_get("start") or {}
    eq = acct.get("equity")
    return {
        "now_et": now_et().strftime("%a %b %d · %I:%M %p ET"),
        "market": {"open": clock.get("is_open", False),
                   "next_open": str(clock.get("next_open", "")), "next_close": str(clock.get("next_close", ""))},
        "paper": config.ALPACA_PAPER, "profile": config.RISK_PROFILE, "crypto_enabled": risk.crypto_enabled(),
        "account": {"equity": eq, "cash": acct.get("cash"),
                    "day_change": (eq / acct["last_equity"] - 1) if eq and acct.get("last_equity") else None,
                    "since_start": (eq / start["equity"] - 1) if eq and start.get("equity") else None},
        "agents": agents, "signals": signals, "events": events,
        "spy": (db.one("SELECT spy FROM equity WHERE spy IS NOT NULL ORDER BY ts DESC LIMIT 1") or {}).get("spy"),
    }


# ---------------------------------------------------------------- dashboard
def equity_series(rng):
    """Portfolio vs S&P 500 for the chart, from the bot's own equity snapshots (taken every 15 minutes, weekends
    included), not Alpaca's portfolio history - that one shows a phantom ~$51k on the day the account was funded
    and, on weekends, only Friday's session. Ranges are rolling windows ending now: 1D = the last 24 hours."""
    span, step = {"1D": (1, "15min"), "1W": (7, "1h"), "1M": (30, "4h"), "3M": (90, "1D"),
                  "1A": (365, "1D")}.get(rng, (30, "4h"))
    since = (datetime.now(timezone.utc) - timedelta(days=span)).isoformat()
    rows = db.query("SELECT ts, equity, spy FROM equity WHERE ts >= ? AND equity > 0 ORDER BY ts", (since,))
    acct = cached("account", 20, lambda: broker().account()) or {}
    if acct.get("equity"):                      # finish on the live value, so "now" is always on the chart
        last_spy = rows[-1]["spy"] if rows else None
        rows.append({"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "equity": acct["equity"], "spy": last_spy})
    if not rows:
        return []
    df = pd.DataFrame(rows)
    df["ts"] = pd.to_datetime(df["ts"], utc=True, format="ISO8601")
    df = df.set_index("ts").resample(step).last().dropna(subset=["equity"])   # one point per slot keeps it readable
    df["spy"] = df["spy"].ffill()
    e0 = df["equity"].iloc[0]
    s0 = df["spy"].dropna().iloc[0] if df["spy"].notna().any() else None
    return [{"t": t.isoformat(), "equity": round(float(r.equity), 2), "eq_pct": round(float(r.equity / e0 - 1), 5),
             "spy_pct": round(float(r.spy / s0 - 1), 5) if s0 and r.spy == r.spy else None}
            for t, r in df.iterrows()]


# window length and slot size for the neuroevolution chart (~36-48 evenly spaced slots per window)
EVO_SPANS = {"6H": (6 * 3600, 600), "1D": (86400, 1800), "1W": (7 * 86400, 4 * 3600), "1M": (30 * 86400, 86400)}


def evolution_series(span="1D", end=None):
    """Training runs grouped into evenly spaced time slots, for one window of the evolution chart.
    Each point = the best unseen-data score a desk reached in that slot (plus how many runs, and
    whether a new champion was promoted). end=None means the most recent window."""
    length, step = EVO_SPANS.get(span, EVO_SPANS["1D"])
    now = datetime.now(timezone.utc).timestamp()
    try:
        t_end = min(now, datetime.fromisoformat(end).timestamp()) if end else now
    except ValueError:
        t_end = now
    end_s = (int(t_end) // step + 1) * step
    start_s = end_s - length
    iso = lambda s: datetime.fromtimestamp(s, timezone.utc).isoformat(timespec="seconds")
    rows = db.query("SELECT ts, desk, best_holdout, champion_holdout, promoted FROM evolution_log "
                    "WHERE ts >= ? AND ts < ? ORDER BY ts", (iso(start_s), iso(end_s)))
    slots = {}
    for r in rows:
        if r["best_holdout"] is None:
            continue
        k = (r["desk"], int((datetime.fromisoformat(r["ts"]).timestamp() - start_s) // step))
        x = slots.setdefault(k, {"v": r["best_holdout"], "runs": 0, "promoted": 0, "champ": None})
        x["v"] = max(x["v"], r["best_holdout"])
        x["runs"] += 1
        x["promoted"] |= int(r["promoted"] or 0)
        x["champ"] = r["best_holdout"] if r["promoted"] else (r["champion_holdout"] if r["champion_holdout"] is not None else x["champ"])
    first = (db.one("SELECT MIN(ts) t FROM evolution_log") or {}).get("t")
    return {"span": span, "step": step, "start": iso(start_s), "end": iso(end_s),
            "has_older": bool(first and first < iso(start_s)), "has_newer": end_s < now,
            "points": [{"desk": d, "t": iso(start_s + (i + .5) * step), "v": round(x["v"], 3), "runs": x["runs"],
                        "promoted": x["promoted"], "champ": None if x["champ"] is None else round(x["champ"], 3)}
                       for (d, i), x in sorted(slots.items(), key=lambda kv: kv[0][1])]}


def dashboard():
    b = broker()
    acct = cached("account", 20, b.account) or {}
    alp = cached("positions", 20, b.alpaca_positions) or {}
    prices = {s: p["price"] for s, p in alp.items()}
    sl = risk.sleeves()
    desks = []
    for d, desk in config.DESKS.items():
        champ = brain.champion(d)
        allt = db.one("SELECT COUNT(*) n, SUM(pnl>0) w, SUM(pnl) p, AVG(pnl_pct) a FROM positions WHERE desk=? AND "
                      "status='closed' AND genome_id != 'legacy'", (d,))
        led = b.ledger(d)
        upl = sum(alp[p["symbol"]]["upl"] for p in led if p["symbol"] in alp)
        desks.append({"id": d, "title": desk.title, "brain": brain.status(champ), "sleeve": sl[d],
                      "size_mult": risk.size_mult(d), "positions": len(led), "max_positions": risk.max_positions(d, acct) if acct else desk.max_positions,
                      "invested": risk.desk_exposure(d, prices), "capital": (acct.get("equity") or 0) * sl[d],
                      "trades": allt["n"], "wins": allt["w"] or 0, "realized": allt["p"] or 0,
                      "avg": allt["a"], "unrealized": upl,
                      "backtest": (champ or {}).get("holdout", {}), "params": (champ or {}).get("params", {}),
                      "champion": (champ or {}).get("id"),
                      "llm_mode": db.kv_get(f"llm_mode:{d}") or config.LLM_TRADE_MODE,
                      "challenger_mode": db.kv_get(f"challenger_mode:{d}") or
                      ("advisory" if config.LLM_TRADE_MODE == "advisory" else "veto")})
    positions = []
    for p in b.ledger(None):
        a = alp.get(p["symbol"], {})
        positions.append({"desk": p["desk"], "symbol": p["symbol"], "status": p["status"], "qty": p["qty"],
                          "entry": p["entry_price"], "price": a.get("price"), "value": a.get("value"),
                          "upl": a.get("upl"), "uplpc": a.get("uplpc"), "stop": p["stop_price"],
                          "target": p["target_price"], "deadline": p["exit_deadline"],
                          "legacy": p["genome_id"] == "legacy"})
    trades = db.query("SELECT desk, symbol, entry_time, exit_time, entry_price, exit_price, pnl, pnl_pct, grade, "
                      "exit_reason, review FROM positions WHERE status='closed' ORDER BY exit_time DESC LIMIT 60")
    for t in trades:
        t["lesson"] = json.loads(t.pop("review") or "{}").get("lesson")
    decisions = db.query("SELECT desk, action, COUNT(*) n, AVG(later_ret) a FROM decisions GROUP BY desk, action")
    debates = db.query("SELECT ts, desk, symbol, verdict, confidence, reasoning, pros, cons, mode, later_ret "
                       "FROM debates ORDER BY id DESC LIMIT 25")
    for x in debates:
        x["pros"], x["cons"] = json.loads(x["pros"] or "[]"), json.loads(x["cons"] or "[]")
    from . import crypto_shadow
    crypto_stage = crypto_shadow.stage(brain.champion("crypto"))
    spy_now = (db.one("SELECT spy FROM equity WHERE spy IS NOT NULL ORDER BY ts DESC LIMIT 1") or {}).get("spy")
    return {"account": acct, "start": db.kv_get("start") or {}, "spy_now": spy_now, "desks": desks,
            "crypto_stage": crypto_stage, "positions": positions,
            "trades": trades, "decisions": decisions, "debates": debates,
            "llm": llm.usage_summary(1), "llm_available": {t: llm.available(t) for t in ("smart", "fast")},
            "challenger": db.kv_get("challenger_scorecard") or {},
            "market": db.kv_get("market_context") or {},
            "lessons": db.query("SELECT ts, desk, grade, text FROM lessons ORDER BY id DESC LIMIT 15")}


# ---------------------------------------------------------------- chat
DESK_RULES = {
    "crypto": "Crypto desk rules: trades Bitcoin, Ethereum, Solana and XRP 24/7 on hourly bars with the smallest "
              "budget in the firm (5% of equity). It only trades when its brain cleared a strict bar on unseen data "
              "AND the portfolio manager approves AND the Challenger says proceed with at least 70% confidence AND "
              "research sentiment isn't negative - otherwise it sits out. Each buy gets a protective stop order at "
              "Alpaca; the bot sells at the target or time limit. The owner can switch it on/off in the Studio.",
    "day": "Day desk rules: trades 9:45am-3:15pm ET on 15-minute bars, every entry is a bracket order (stop-loss + "
           "take-profit at Alpaca), and every position is sold by 3:50pm ET - nothing is held overnight.",
    "swing": "Swing desk rules: decides from daily bars between 9:45am and 3:30pm ET, bracket orders (GTC stop-loss + "
             "take-profit), and sells anything still open when its holding-time limit is reached.",
    "long": "Long-term desk rules: decides once a day from 10:00am ET, bracket orders on new buys, and at each "
            "rebalance sells adopted 'legacy' positions (from the old bot) that its brain would not buy today and trims "
            "the desk back to its capital share. Legacy positions have NO stop-loss orders. The first rebalance happens "
            "at the next market open after 10:00am ET.",
}


def _context(aid):
    p = AGENTS[aid]
    d = p["desk"]
    acct = cached("account", 20, lambda: broker().account()) or {}
    clock = cached("clock", 60, lambda: broker().clock()) or {}
    act = db.kv_get(f"activity:{aid}") or {}
    mc = db.kv_get("market_context") or {}
    start = db.kv_get("start") or {}
    lines = [f"Now: {now_et():%A %b %d %Y %I:%M %p} New York time. US stock market is "
             f"{'OPEN' if clock.get('is_open') else 'CLOSED'}. Paper trading (fake money).",
             f"Account equity ${acct.get('equity', 0):,.2f}, cash ${acct.get('cash', 0):,.2f}"
             + (f", {100 * (acct['equity'] / start['equity'] - 1):+.2f}% since the system started" if start.get("equity") and acct.get("equity") else ""),
             f"Market backdrop: {mc.get('summary', 'n/a')} (risk {mc.get('risk', '?')}/5)",
             f"What you are doing right now: {act.get('text', 'n/a')}"]
    ev = db.query("SELECT ts, msg FROM events WHERE agent=? ORDER BY id DESC LIMIT 6", (aid,))
    if ev:
        lines.append("Your recent notable events: " + " | ".join(f"{e['ts'][5:16]} {e['msg']}" for e in ev))
    desks = [d] if d else list(config.DESKS)
    for dk in desks:
        champ = brain.champion(dk)
        h = (champ or {}).get("holdout", {})
        n = db.one("SELECT COUNT(*) n, SUM(pnl>0) w, SUM(pnl) p FROM positions WHERE desk=? AND status='closed' "
                   "AND genome_id != 'legacy'", (dk,))
        lines.append(DESK_RULES[dk])
        if dk == "crypto":
            from . import crypto_shadow
            lines.append("Crypto training stage right now: " + crypto_shadow.stage(brain.champion("crypto"))["label"])
        lines.append(f"[{dk} desk] brain status {brain.status(champ)}; capital share {risk.sleeves()[dk]:.0%}; "
                     f"live closed trades {n['n']} ({n['w'] or 0} wins, P&L ${n['p'] or 0:,.2f}); backtest on unseen "
                     f"data: {h.get('trades', 0)} trades, {100 * h.get('total', 0):+.1f}%, win rate "
                     f"{100 * h.get('winrate', 0):.0f}%, sharpe {h.get('sharpe')}; rules {champ.get('params') if champ else 'n/a'}")
        pos = broker().ledger(dk)
        if pos:
            lines.append(f"[{dk}] open positions: " + ", ".join(
                f"{x['symbol']} x{x['qty']:.4g} @ {x['entry_price'] or 0:.2f} (stop {x['stop_price']}, target "
                f"{x['target_price']}{', legacy from old bot' if x['genome_id'] == 'legacy' else ''})" for x in pos))
        tr = db.query("SELECT symbol, pnl_pct, grade, exit_reason, review FROM positions WHERE desk=? AND "
                      "status='closed' ORDER BY exit_time DESC LIMIT 5", (dk,))
        for t in tr:
            lines.append(f"[{dk}] closed {t['symbol']} {100 * (t['pnl_pct'] or 0):+.2f}% grade {t['grade']} "
                         f"({t['exit_reason']}); lesson: {json.loads(t['review'] or '{}').get('lesson', '-')}")
    if p["kind"] == "researcher":
        for b in db.query("SELECT symbol, quant_score, sentiment, summary, catalysts, risks FROM briefs WHERE desk=? "
                          "AND id IN (SELECT MAX(id) FROM briefs WHERE desk=? GROUP BY symbol) "
                          "ORDER BY created DESC, quant_score DESC LIMIT 12", (d, d)):
            lines.append(f"Your brief {b['symbol']}: quant {b['quant_score']:+.2f}, sentiment {b['sentiment']}, "
                         f"{b['summary'] or ''} catalysts: {b['catalysts'] or '-'} risks: {b['risks'] or '-'}")
    if p["kind"] == "trader":
        for x in db.query("SELECT ts, symbol, action, reason FROM decisions WHERE desk=? ORDER BY id DESC LIMIT 10", (d,)):
            lines.append(f"Decision {x['ts'][5:16]} {x['symbol']}: {x['action']} - {x['reason'] or ''}"[:300])
    if p["kind"] == "challenger":
        lines.append(f"Your scorecard: {(db.kv_get('challenger_scorecard') or {}).get('text', 'none yet')}")
        for x in db.query("SELECT ts, desk, symbol, verdict, reasoning, pros, cons FROM debates ORDER BY id DESC LIMIT 8"):
            lines.append(f"Debate {x['ts'][5:16]} {x['desk']} {x['symbol']}: {x['verdict']} - {x['reasoning']} "
                         f"PROS {x['pros']} CONS {x['cons']}"[:500])
    if p["kind"] == "lab":
        for x in db.query("SELECT ts, desk, generations, best_holdout, promoted, note FROM evolution_log "
                          "ORDER BY id DESC LIMIT 8"):
            lines.append(f"Evolution {x['ts'][5:16]} {x['desk']}: {x['generations']} gens, "
                         f"{'promoted' if x['promoted'] else 'kept'} - {x['note']}")
    if p["kind"] == "manager":
        rep = config.REPORTS_DIR / "LATEST.md"
        if rep.exists():
            lines.append("Latest report (excerpt):\n" + rep.read_text(encoding="utf-8")[:2500])
        for x in db.query("SELECT desk, grade, text FROM lessons ORDER BY id DESC LIMIT 6"):
            lines.append(f"Lesson [{x['desk']} {x['grade']}] {x['text']}")
    return "\n".join(lines)


def chat(aid, message):
    p = AGENTS[aid]
    db.execute("INSERT INTO chats (agent, ts, role, text) VALUES (?,?,?,?)", (aid, db.now_iso(), "user", message[:2000]))
    hist = db.query("SELECT role, text FROM chats WHERE agent=? ORDER BY id DESC LIMIT 13", (aid,))[::-1][:-1]
    transcript = "\n".join(f"{'Owner' if h['role'] == 'user' else p['name']}: {h['text']}" for h in hist[-12:])
    system = (f"You are {p['name']}, the {p['role']} in a cozy little AI trading studio (paper trading on Alpaca). "
              f"Personality: {p['bio']} You are chatting with the studio's owner. Be warm, natural and concise "
              "(usually 2-6 sentences), speak in first person as yourself, and be honest: only state facts found in "
              "the live context below; if something isn't there, say you don't know. Backtest numbers are "
              "simulations, not real money. You cannot place, cancel or change trades or settings from this chat - "
              "if asked, explain the owner can use the Alpaca app, edit .env, or create the STOP_TRADING file. "
              "Never promise profits. Write in plain conversational text: short paragraphs or a few bullet points, "
              "no tables and no headings.\n\nLIVE CONTEXT:\n" + _context(aid))
    user = (f"Conversation so far:\n{transcript}\n\n" if transcript else "") + f"Owner: {message}\n{p['name']}:"
    res = llm.chat(system, user, tier="smart", max_tokens=700, want_json=False, agent=aid, cache_min=0)
    text = (res or {}).get("text", "").strip() or \
        "(I can't reach any AI model right now - all of them are busy or out of quota. Try again in a minute.)"
    db.execute("INSERT INTO chats (agent, ts, role, text) VALUES (?,?,?,?)", (aid, db.now_iso(), "bot", text))
    return {"text": text, "model": f"{res['provider']}:{res['model']}" if res else None}


# ---------------------------------------------------------------- reports
def reports_list():
    files = sorted(config.REPORTS_DIR.glob("*_report.md"), reverse=True)
    return [{"name": f.name, "title": f.stem.replace("_report", "").replace("_", " "),
             "size": f.stat().st_size} for f in files[:200]]


_report_lock = threading.Lock()


def new_report():
    if not _report_lock.acquire(blocking=False):
        return {"busy": True}
    try:
        from . import report
        path = report.write(broker(), db.kv_get("last_report_at"))
        return {"name": path.name}
    finally:
        _report_lock.release()


# ---------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _authorized(self):
        if not PASSWORD:
            return True
        import base64
        import hmac
        h = self.headers.get("Authorization", "")
        if h.startswith("Basic "):
            try:
                _, pw = base64.b64decode(h[6:]).decode().split(":", 1)
                if hmac.compare_digest(pw, PASSWORD):
                    return True
            except Exception:
                pass
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="The Studio"')
        self.end_headers()
        return False

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if not self._authorized():
            return
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            if u.path == "/api/state":
                return self._send(200, state())
            if u.path == "/api/dashboard":
                return self._send(200, cached("dashboard", 15, dashboard))
            if u.path == "/api/equity":
                rng = q.get("range", "1M")
                return self._send(200, cached(f"equity:{rng}", 60, lambda: equity_series(rng)) or [])
            if u.path == "/api/evolution":
                span, end = q.get("span", "1D"), q.get("end") or None
                return self._send(200, cached(f"evo:{span}:{end}", 10, lambda: evolution_series(span, end)))
            if u.path == "/api/chat/history":
                aid = q.get("agent", "")
                return self._send(200, db.query("SELECT role, text, ts FROM chats WHERE agent=? ORDER BY id DESC LIMIT 60",
                                                (aid,))[::-1])
            if u.path == "/api/reports":
                return self._send(200, reports_list())
            if u.path == "/api/report":
                name = q.get("name", "LATEST.md")
                f = (config.REPORTS_DIR / name).resolve()
                if f.parent != config.REPORTS_DIR.resolve() or not f.exists():
                    return self._send(404, {"error": "not found"})
                return self._send(200, f.read_bytes(), "text/plain; charset=utf-8")
            path = "index.html" if u.path in ("/", "") else u.path.lstrip("/")
            f = (STATIC / path).resolve()
            if STATIC.resolve() not in f.parents or not f.is_file():
                return self._send(404, {"error": "not found"})
            ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype.endswith("javascript"):
                ctype += "; charset=utf-8"
            return self._send(200, f.read_bytes(), ctype)
        except Exception as e:
            L.warning("GET %s failed: %s", u.path, e)
            return self._send(500, {"error": str(e)})

    def do_POST(self):
        if not self._authorized():
            return
        u = urlparse(self.path)
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
            if u.path == "/api/chat":
                aid = body.get("agent")
                msg = str(body.get("message", "")).strip()
                if aid not in AGENTS or not msg:
                    return self._send(400, {"error": "bad request"})
                return self._send(200, chat(aid, msg))
            if u.path == "/api/report/new":
                return self._send(200, new_report())
            if u.path == "/api/crypto":
                on = bool(body.get("enabled"))
                db.kv_set("crypto_enabled", on)
                db.event("manager", f"Owner switched the crypto desk {'ON' if on else 'OFF'} in the Studio")
                return self._send(200, {"enabled": on})
            return self._send(404, {"error": "not found"})
        except Exception as e:
            L.warning("POST %s failed: %s", u.path, e)
            return self._send(500, {"error": str(e)})


def serve_forever():
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    httpd.daemon_threads = True
    L.info("Studio open at http://%s:%d%s", "localhost" if HOST == "127.0.0.1" else HOST, PORT,
           " (password protected)" if PASSWORD else "")
    httpd.serve_forever()


def start_background():
    t = threading.Thread(target=serve_forever, name="studio", daemon=True)
    t.start()
    return t
