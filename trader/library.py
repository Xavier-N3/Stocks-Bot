"""The research library: one readable page per stock in library/<SYMBOL>.md,
rebuilt from the database whenever a researcher studies that stock."""
import json
from datetime import datetime, timezone

from . import config, db, universe, web


def _age(ts):
    try:
        mins = (datetime.now(timezone.utc) - datetime.fromisoformat(ts)).total_seconds() / 60
    except Exception:
        return ""
    return f"{mins:.0f}m" if mins < 120 else (f"{mins / 60:.0f}h" if mins < 2880 else f"{mins / 1440:.0f}d")


def write(symbol: str, numbers: dict = None):
    info = universe.info(symbol)
    lines = [f"# {symbol} - {info.get('name', '')}", "",
             f"Sector: {info.get('sector') or 'n/a'}  ·  updated {datetime.now().strftime('%Y-%m-%d %H:%M')}", ""]
    if numbers:
        lines += ["## Latest numbers", "", " | ".join(f"{k}: {v}" for k, v in numbers.items()), ""]
    f = web.get_fundamentals(symbol)
    if f:
        lines += ["## Fundamentals (SEC filings, latest fiscal year)", ""]
        for k, v in f.items():
            if v is None:
                continue
            if k in ("revenue",):
                v = f"${v / 1e9:,.2f}B"
            elif isinstance(v, float) and k != "eps":
                v = f"{v * 100:.1f}%"
            lines.append(f"- {k.replace('_', ' ')}: {v}")
        lines.append("")
    briefs = db.query("SELECT * FROM briefs WHERE symbol=? AND id IN (SELECT MAX(id) FROM briefs WHERE symbol=? "
                      "GROUP BY desk)", (symbol, symbol))
    if briefs:
        lines += ["## Desk research", ""]
        for b in briefs:
            sent = "n/a" if b["sentiment"] is None else f"{b['sentiment']:+.2f}"
            lines += [f"### {config.DESKS[b['desk']].title} ({_age(b['created'])} ago)",
                      f"- Quant score: {b['quant_score']:.3f}  ·  AI sentiment: {sent}  ·  model: {b['model'] or 'none'}",
                      f"- Summary: {b['summary'] or '-'}",
                      f"- Catalysts: {b['catalysts'] or '-'}",
                      f"- Risks: {b['risks'] or '-'}", ""]
    news = db.query("SELECT ts, source, headline FROM news WHERE symbol=? ORDER BY ts DESC LIMIT 10", (symbol,))
    if news:
        lines += ["## Recent headlines", ""] + [f"- ({_age(n['ts'])} ago, {n['source']}) {n['headline']}" for n in news] + [""]
    trades = db.query("SELECT desk, entry_time, exit_time, pnl_pct, grade, exit_reason FROM positions WHERE symbol=? "
                      "AND status='closed' ORDER BY exit_time DESC LIMIT 10", (symbol,))
    if trades:
        lines += ["## Our trades in this stock", ""]
        lines += [f"- {t['desk']}: {t['entry_time'][:16]} -> {t['exit_time'][:16]}  {100 * (t['pnl_pct'] or 0):+.2f}%  "
                  f"grade {t['grade'] or '?'} ({t['exit_reason']})" for t in trades]
    (config.LIBRARY_DIR / f"{symbol.replace('/', '_')}.md").write_text("\n".join(lines), encoding="utf-8")


def write_index():
    rows = db.query("SELECT symbol, desk, MAX(created) c, quant_score, sentiment, summary FROM briefs "
                    "WHERE created > datetime('now','-7 days') GROUP BY symbol, desk ORDER BY c DESC")
    lines = ["# Research library", "", "Every stock the researchers studied in the last 7 days. "
             "Open any SYMBOL.md file in this folder for the full page.", "",
             "| Symbol | Desk | When | Quant | AI sentiment | Summary |", "|---|---|---|---|---|---|"]
    for r in rows[:300]:
        sent = "" if r["sentiment"] is None else f"{r['sentiment']:+.2f}"
        summ = (r["summary"] or "").replace("|", "/")[:120]
        lines.append(f"| [{r['symbol']}]({r['symbol']}.md) | {r['desk']} | {r['c'][:16]} | {r['quant_score']:.2f} | {sent} | {summ} |")
    (config.LIBRARY_DIR / "INDEX.md").write_text("\n".join(lines), encoding="utf-8")
