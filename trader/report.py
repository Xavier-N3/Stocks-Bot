"""The 8-hour report: everything that happened, in plain language, saved to
reports/ (newest copy always at reports/LATEST.md)."""
import json
from datetime import datetime, timedelta, timezone

from . import brain, config, db, llm, risk
from .timeutil import ET, now_et


def _pct(x, digits=2):
    return "n/a" if x is None else f"{x * 100:+.{digits}f}%"


def _money(x):
    return "n/a" if x is None else f"${x:,.2f}"


def _t(iso):
    try:
        return datetime.fromisoformat(iso).astimezone(ET).strftime("%m-%d %H:%M")
    except Exception:
        return iso or ""


def _table(headers, rows):
    if not rows:
        return "_none_\n"
    out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out) + "\n"


def build(broker, since_iso=None) -> str:
    now = datetime.now(timezone.utc)
    since_iso = since_iso or (now - timedelta(hours=8)).isoformat()
    acct = broker.account()
    alp = broker.alpaca_positions()
    start = db.kv_get("start") or {}
    prev_eq = db.one("SELECT equity, spy FROM equity WHERE ts <= ? ORDER BY ts DESC LIMIT 1", (since_iso,))
    last_spy = db.one("SELECT spy FROM equity WHERE spy IS NOT NULL ORDER BY ts DESC LIMIT 1")
    spy_now = last_spy["spy"] if last_spy else None
    eq = acct["equity"]

    L = [f"# Trading report - {now_et():%A %b %d, %Y %I:%M %p} ET", "",
         f"**Mode: {'PAPER (fake money)' if config.ALPACA_PAPER else 'LIVE MONEY'} · risk profile {config.RISK_PROFILE.upper()}**  ·  period since {_t(since_iso)} ET", ""]

    # ---- account
    since_start = eq / start["equity"] - 1 if start.get("equity") else None
    spy_start = spy_now / start["spy"] - 1 if spy_now and start.get("spy") else None
    L += ["## Account", "",
          f"- Equity: **{_money(eq)}**  (this period {_pct(eq / prev_eq['equity'] - 1 if prev_eq else None)}, "
          f"since system start {_pct(since_start)})",
          f"- S&P 500 (SPY) since system start: {_pct(spy_start)}  ->  "
          + ("**beating the market**" if since_start is not None and spy_start is not None and since_start > spy_start
             else "not beating the market yet" if since_start is not None and spy_start is not None else "n/a"),
          f"- Cash: {_money(acct['cash'])}  ·  positions: {len(alp)}  ·  today's change vs yesterday's close: "
          f"{_pct(eq / acct['last_equity'] - 1 if acct['last_equity'] else None)}", ""]

    # ---- desks
    sl = risk.sleeves()
    prices = {s: p["price"] for s, p in alp.items()}
    rows = []
    for d, desk in config.DESKS.items():
        champ = brain.champion(d)
        allt = db.one("SELECT COUNT(*) n, SUM(pnl>0) w, SUM(pnl) p, AVG(pnl_pct) a FROM positions WHERE desk=? AND "
                      "status='closed' AND genome_id != 'legacy'", (d,))
        per = db.one("SELECT COUNT(*) n, SUM(pnl) p FROM positions WHERE desk=? AND status='closed' AND exit_time>=?",
                     (d, since_iso))
        upl = sum(alp[p["symbol"]]["upl"] for p in broker.ledger(d, ("open",)) if p["symbol"] in alp)
        h = (champ or {}).get("holdout", {})
        mode = db.kv_get(f"llm_mode:{d}") or config.LLM_TRADE_MODE
        rows.append([desk.title, brain.status(champ), f"{sl[d] * 100:.0f}% x{risk.size_mult(d)}",
                     len(broker.ledger(d)), _money(risk.desk_exposure(d, prices)), _money(per["p"] or 0),
                     per["n"], _money(upl),
                     f"{allt['n']} / {100 * (allt['w'] or 0) / allt['n']:.0f}%" if allt["n"] else "0",
                     _pct(allt["a"]), f"{_pct(h.get('avg'))} ({h.get('trades', 0)} trades)", mode])
    L += ["## Desks", "",
          _table(["Desk", "Brain", "Capital", "Positions", "Invested", "Realized (period)", "Closed (period)",
                  "Unrealized", "All-time trades / win rate", "Live avg/trade", "Backtest avg/trade (unseen data)",
                  "AI mode"], rows),
          "_Brain: **active** = proved an edge on data it never trained on; **probation** = no proven edge yet, "
          f"trading at {config.PROBATION_SIZE:.0%} size; **untrained** = first evolution still running._", ""]

    from . import crypto_shadow
    L += [f"**Crypto desk:** {'ON' if risk.crypto_enabled() else 'OFF'} - "
          f"{crypto_shadow.stage(brain.champion('crypto'))['label']}",
          f"**Forex desk:** " + (f"{crypto_shadow.stage(brain.champion('forex'))['label']}" if config.FOREX_ENABLED
                                 else "waiting for an OANDA practice account (OANDA_API_TOKEN in .env)"), ""]

    # ---- closed trades
    closed = db.query("SELECT * FROM positions WHERE status='closed' AND exit_time>=? ORDER BY exit_time", (since_iso,))
    rows = []
    for p in closed:
        rv = json.loads(p["review"] or "{}")
        rows.append([p["desk"], p["symbol"], f"{_t(p['entry_time'])} -> {_t(p['exit_time'])}",
                     f"{p['entry_price']:.2f} -> {p['exit_price']:.2f}", _pct(p["pnl_pct"]), _money(p["pnl"]),
                     f"{rv.get('r_mult', 0):+.1f}R" if rv else "", p["grade"] or "pending",
                     "success" if (p["pnl"] or 0) > 0 else "failure", p["exit_reason"]])
    wins = sum(1 for p in closed if (p["pnl"] or 0) > 0)
    L += [f"## Trades closed this period ({len(closed)}: {wins} successes, {len(closed) - wins} failures)", "",
          _table(["Desk", "Symbol", "Held", "Price", "P&L %", "P&L $", "R", "Grade", "Result", "Exit"], rows),
          "_R = profit measured in units of the risk taken (stop distance). Grades: A >= 1.5R, B >= 0.5R, "
          "C = scratch, D = loss within plan, F = loss worse than planned (gap through stop)._", ""]

    # ---- open positions
    rows = []
    for p in broker.ledger(None, ("pending", "open", "closing")):
        a = alp.get(p["symbol"], {})
        rows.append([p["desk"], p["symbol"], p["status"], f"{p['qty']:.4g}", f"{p['entry_price'] or 0:.2f}",
                     f"{a.get('price', 0):.2f}", _pct(a.get("uplpc")), p["stop_price"] or "-", p["target_price"] or "-",
                     _t(p["exit_deadline"]) if p["exit_deadline"] else ("legacy" if p["genome_id"] == "legacy" else "-")])
    L += ["## Open positions", "",
          _table(["Desk", "Symbol", "Status", "Qty", "Entry", "Now", "Unrealized", "Stop", "Target", "Exit by"], rows), ""]

    # ---- learning
    lessons = db.query("SELECT desk, grade, text FROM lessons WHERE ts>=? ORDER BY id", (since_iso,))
    evo = db.query("SELECT * FROM evolution_log WHERE ts>=? ORDER BY id", (since_iso,))
    L += ["## What the bots learned", "", "**Lessons from reviewed trades** (traders read these before every decision):", ""]
    L += [f"- [{l['desk']}, {l['grade']}] {l['text']}" for l in lessons] or ["- none this period"]
    L += ["", "**Neuroevolution runs** (each = a population of 32 brains bred and tested on history):", ""]
    L += [f"- {_t(e['ts'])} {e['desk']}: {e['generations']} generations, best unseen-data fitness "
          f"{e['best_holdout']:.3f} - {'PROMOTED' if e['promoted'] else 'kept champion'}: {e['note']}" for e in evo] \
        or ["- none this period"]
    L.append("")
    for d in config.DESKS:
        c = brain.champion(d)
        if c:
            h, t, pr = c.get("holdout", {}), c.get("train", {}), c.get("params", {})
            L.append(f"- **{d} champion** `{c['id']}`: on unseen data {h.get('trades')} trades, win rate "
                     f"{100 * h.get('winrate', 0):.0f}%, total {_pct(h.get('total'))}, sharpe {h.get('sharpe')}, "
                     f"max drawdown {_pct(h.get('maxdd'))}  ·  training sharpe {t.get('sharpe')}  ·  rules: stop "
                     f"{pr.get('stop', 0):.1f}xATR, target {pr.get('target', 0):.1f}xATR, hold {pr.get('hold')} bars")
    L.append("")

    # ---- AI usage + value
    usage = llm.usage_summary(1)
    L += ["## AI models used today", "",
          _table(["Provider", "Model", "Requests", "Tokens", "Errors"],
                 [[u["provider"], u["model"], u["requests"], f"{u['tokens']:,}", u["errors"]] for u in usage])]
    vrows = []
    for d in config.DESKS:
        s = {r["action"]: r for r in db.query("SELECT action, COUNT(*) n, AVG(later_ret) a FROM decisions WHERE desk=? "
                                             "AND later_ret IS NOT NULL GROUP BY action", (d,))}
        if s:
            vrows.append([d, s.get("buy", {}).get("n", 0), _pct(s.get("buy", {}).get("a")),
                          s.get("veto", {}).get("n", 0), _pct(s.get("veto", {}).get("a")),
                          s.get("challenged", {}).get("n", 0), _pct(s.get("challenged", {}).get("a"))])
    L += ["**Is the AI's veto helping?** (what happened afterwards to stocks it approved vs. rejected)", "",
          _table(["Desk", "Bought", "Avg move after", "PM vetoed", "Avg move after", "Challenger rejected",
                  "Avg move after"], vrows),
          "_If vetoed stocks keep doing better than bought ones, the manager automatically demotes the AI to advisory._", ""]

    # ---- debates
    debs = db.query("SELECT * FROM debates WHERE ts>=? ORDER BY id DESC LIMIT 12", (since_iso,))
    L += ["## Challenger debates (pros vs cons before each trade)", ""]
    for x in debs:
        L.append(f"- {_t(x['ts'])} **{x['desk']} {x['symbol']}** -> **{x['verdict'].upper()}** "
                 f"({x['mode']}): {x['reasoning']}")
        L.append(f"  - Pros: {'; '.join(json.loads(x['pros'] or '[]'))}")
        L.append(f"  - Cons: {'; '.join(json.loads(x['cons'] or '[]'))}")
    if not debs:
        L.append("- no trades were debated this period")
    card = db.kv_get("challenger_scorecard") or {}
    L += ["", f"_Challenger scorecard: {card.get('text', 'n/a')}_", ""]

    # ---- research
    ctx = db.kv_get("market_context") or {}
    L += ["## Research highlights", "", f"**Market backdrop:** {ctx.get('summary', 'n/a')} (risk {ctx.get('risk', '?')}/5)", ""]
    for d in config.DESKS:
        top = db.query("SELECT symbol, quant_score, sentiment, summary FROM briefs WHERE desk=? AND created>=? AND id IN "
                       "(SELECT MAX(id) FROM briefs WHERE desk=? GROUP BY symbol) ORDER BY quant_score DESC LIMIT 5",
                       (d, since_iso, d))
        if top:
            L.append(f"**{config.DESKS[d].title}:**")
            L += [f"- {b['symbol']} (quant {b['quant_score']:+.2f}, AI sentiment "
                  f"{'n/a' if b['sentiment'] is None else format(b['sentiment'], '+.2f')}): {b['summary'] or '-'}" for b in top]
            L.append("")
    L.append("Full per-stock pages: `library/INDEX.md`\n")

    # ---- health
    rows = []
    for name in ["day_researcher", "day_trader", "swing_researcher", "swing_trader", "long_researcher",
                 "long_trader", "crypto_researcher", "crypto_trader", "forex_researcher", "forex_trader", "challenger",
                 "manager", "lab"]:
        hb = db.kv_get(f"heartbeat:{name}") or {}
        rows.append([name, _t(hb.get("at", "")), hb.get("status", "never ran")[:80]])
    errs = db.query("SELECT ts, agent, level, msg FROM events WHERE ts>=? AND level IN ('error','warning') "
                    "ORDER BY id DESC LIMIT 12", (since_iso,))
    notes = db.query("SELECT ts, agent, msg FROM events WHERE ts>=? AND level='info' AND msg NOT LIKE '%started' "
                     "ORDER BY id DESC LIMIT 15", (since_iso,))
    L += ["## System health", "", _table(["Agent", "Last heartbeat", "Status"], rows)]
    L += ["**Notable events:**", ""] + ([f"- {_t(e['ts'])} {e['agent']}: {e['msg']}" for e in notes] or ["- none"])
    L += ["", "**Warnings / errors:**", ""] + ([f"- {_t(e['ts'])} {e['agent']} ({e['level']}): {e['msg'][:200]}"
                                              for e in errs] or ["- none"])
    L += ["", "---", "_Reminder: paper fills are friendlier than real ones (no partial fills, little slippage). "
          "Judge the system over months and hundreds of trades, and against simply holding SPY, before risking real money._"]
    return "\n".join(L)


def write(broker, since_iso=None):
    body = build(broker, since_iso)
    summary = llm.chat(
        "You are the head of a trading firm writing the top of a report for the owner, who is not technical. "
        "Plain English, honest, no hype. If results are weak or too few trades exist to judge, say so.",
        body[:12000] + "\n\nWrite a 4-6 sentence executive summary of this report: real performance vs the S&P 500, "
        "which desks did well or badly and why, what the system learned, and anything that needs the owner's "
        "attention. IMPORTANT: 'backtest' / 'unseen data' / champion numbers are SIMULATIONS on past data, not real "
        "results - never present them as money made. Start directly with the summary, no preamble.",
        tier="smart", max_tokens=500, want_json=False, agent="manager", cache_min=0)
    if summary and summary.get("text"):
        head, rest = body.split("\n## Account", 1)
        body = (f"{head}\n## Executive summary\n\n{summary['text'].strip()}\n\n_(written by "
                f"{summary['provider']}:{summary['model']})_\n\n## Account{rest}")
    stamp = now_et().strftime("%Y-%m-%d_%H%M")
    path = config.REPORTS_DIR / f"{stamp}_report.md"
    path.write_text(body, encoding="utf-8")
    (config.REPORTS_DIR / "LATEST.md").write_text(body, encoding="utf-8")
    return path
