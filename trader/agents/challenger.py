"""The Challenger: a devil's advocate that debates every trade before it is
placed. For each pick the portfolio manager approved, it builds the strongest
case FOR and AGAINST, weighs them, and rules: proceed, reduce, or reject.
Between trades it keeps a scorecard of whether its rejections were right, and
the manager demotes it to advisory if its rejections keep missing winners."""
import json
import threading
from datetime import datetime, timedelta, timezone

from .. import config, db, llm
from .base import Agent

_lock = threading.Lock()


def mode(desk) -> str:
    return db.kv_get(f"challenger_mode:{desk}") or ("advisory" if config.LLM_TRADE_MODE == "advisory" else "veto")


def review(desk_name: str, picks: list, agent_name: str) -> dict:
    """picks: [{symbol, price, score, thr, stop_pct, target_pct, brief, pm}] ->
    {symbol: {verdict, size, pros, cons, reasoning}}. Empty dict = no AI available (trade proceeds)."""
    if not picks or config.LLM_TRADE_MODE == "off":
        return {}
    desk = config.DESKS[desk_name]
    db.kv_set("activity:challenger", {"at": db.now_iso(), "text": f"Debating {len(picks)} {desk_name} trade(s): "
                                      + ", ".join(p["symbol"] for p in picks)})
    ctx = db.kv_get("market_context") or {}
    lessons = db.query("SELECT text FROM lessons WHERE desk=? ORDER BY id DESC LIMIT 5", (desk_name,))
    card = db.kv_get("challenger_scorecard") or {}
    lines = []
    for p in picks:
        b = p.get("brief") or {}
        data = json.loads(b.get("data") or "{}") if isinstance(b.get("data"), str) else {}
        lines.append(
            f"{p['symbol']} @ ${p['price']:.2f} | quant {p['score']:+.3f} (threshold {p['thr']:+.3f}) | stop "
            f"-{p['stop_pct'] * 100:.1f}% target +{p['target_pct'] * 100:.1f}% | numbers {json.dumps(data.get('numbers', {}))}\n"
            f"  research: {b.get('summary') or '-'} | sentiment {b.get('sentiment')} | catalysts: {b.get('catalysts') or '-'}"
            f" | risks: {b.get('risks') or '-'}\n"
            f"  headlines: {'; '.join((data.get('headlines') or [])[:4]) or '-'}\n"
            f"  portfolio manager said: {(p.get('pm') or {}).get('reason') or 'approved'}")
    system = ("You are the Challenger on a trading desk: a sharp, fair devil's advocate. Before any order is sent you "
              "build the STRONGEST case for the trade and the STRONGEST case against it, then weigh them honestly. "
              "The quant model has a tested statistical edge, so normal uncertainty is not enough to reject: reject "
              "only when concrete evidence in the cons clearly outweighs the pros (e.g. earnings inside the holding "
              "period, risk/reward worse than 1:1 with a real catalyst against it, broken news, extreme extension). "
              "Use 'reduce' when the case is genuinely balanced. Reply with JSON only.")
    user = (f"Desk: {desk.title}. Market backdrop: {ctx.get('summary', 'n/a')} (risk {ctx.get('risk', '?')}/5)\n"
            f"Your track record: {card.get('text', 'no record yet')}\n"
            "Recent desk lessons:\n" + ("\n".join(f"- {l['text']}" for l in lessons) or "- none") +
            "\n\nProposed trades:\n" + "\n".join(lines) +
            '\n\nJSON: {"debates": [{"symbol": "X", "pros": ["...", "..."], "cons": ["...", "..."], '
            '"verdict": "proceed" | "reduce" | "reject", "confidence": 0..1, "reasoning": "one or two sentences"}]}')
    res = llm.chat(system, user, tier="smart", max_tokens=1400, agent="challenger", cache_min=5)
    out = {}
    if not res or not isinstance(res.get("json"), dict):
        return out
    m = mode(desk_name)
    for it in res["json"].get("debates", []) or []:
        sym = str(it.get("symbol", "")).upper()
        if sym not in {p["symbol"] for p in picks}:
            continue
        verdict = str(it.get("verdict", "proceed")).lower()
        verdict = verdict if verdict in ("proceed", "reduce", "reject") else "proceed"
        pros = [str(x)[:200] for x in (it.get("pros") or [])][:5]
        cons = [str(x)[:200] for x in (it.get("cons") or [])][:5]
        try:
            conf = max(0.0, min(1.0, float(it.get("confidence", 0.5))))
        except (TypeError, ValueError):
            conf = 0.5
        reasoning = str(it.get("reasoning", ""))[:400]
        price = next(p["price"] for p in picks if p["symbol"] == sym)
        db.execute("INSERT INTO debates (ts, desk, symbol, price, pros, cons, verdict, confidence, reasoning, mode, model) "
                   "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                   (db.now_iso(), desk_name, sym, price, json.dumps(pros), json.dumps(cons), verdict, conf, reasoning,
                    m, f"{res['provider']}:{res['model']}"))
        out[sym] = {"verdict": verdict, "size": 0.6 if verdict == "reduce" else 1.0, "pros": pros, "cons": cons,
                    "confidence": conf, "reasoning": reasoning, "mode": m}
    rej = [s for s, v in out.items() if v["verdict"] == "reject"]
    db.kv_set("activity:challenger", {"at": db.now_iso(), "text": f"Ruled on {desk_name}: " + ", ".join(
        f"{s} {v['verdict']}" for s, v in out.items())})
    if rej:
        db.event("challenger", f"[{desk_name}] argued against {', '.join(rej)}" + (" (advisory only)" if m != "veto" else ""))
    return out


class Challenger(Agent):
    """Between debates: keep score of past rulings against what prices did next."""
    name = "challenger"
    title = "Challenger (devil's advocate)"

    def wait_seconds(self):
        return 1800

    def step(self):
        horizon = {"day": 6, "swing": 24 * 7, "long": 24 * 30}
        rows = []
        for d, hrs in horizon.items():
            cutoff = (datetime.now(timezone.utc) - timedelta(hours=hrs)).isoformat()
            rows += db.query("SELECT id, symbol, price FROM debates WHERE desk=? AND later_ret IS NULL AND ts < ? LIMIT 100",
                             (d, cutoff))
        if rows:
            from ..marketdata import MarketData
            snaps = MarketData().snapshots(sorted({r["symbol"] for r in rows}))
            for r in rows:
                px = (snaps.get(r["symbol"]) or {}).get("price")
                if px and r["price"]:
                    db.execute("UPDATE debates SET later_ret=? WHERE id=?", (px / r["price"] - 1, r["id"]))
        stats = {r["verdict"]: r for r in db.query(
            "SELECT verdict, COUNT(*) n, AVG(later_ret) a FROM debates WHERE later_ret IS NOT NULL GROUP BY verdict")}
        parts = [f"{v}: {s['n']} calls, avg move after {100 * (s['a'] or 0):+.2f}%" for v, s in stats.items()]
        text = "; ".join(parts) or "no scored rulings yet"
        db.kv_set("challenger_scorecard", {"at": db.now_iso(), "text": text, "stats": stats})
        n = db.one("SELECT COUNT(*) n FROM debates")["n"]
        db.kv_set("activity:challenger", {"at": db.now_iso(), "text": f"Scoring my past rulings ({n} debates so far)"})
        # if rejected stocks keep beating the ones let through, the challenger is hurting: demote it
        rj, ok = stats.get("reject"), stats.get("proceed")
        if rj and ok and rj["n"] >= 25 and ok["n"] >= 25:
            for d in config.DESKS:
                cur = mode(d)
                if rj["a"] > ok["a"] + 0.002 and cur == "veto":
                    db.kv_set(f"challenger_mode:{d}", "advisory")
                    db.event(self.name, f"[{d}] my rejections missed winners on average - switched to advisory", "warning")
                elif rj["a"] < ok["a"] - 0.002 and cur == "advisory":
                    db.kv_set(f"challenger_mode:{d}", "veto")
                    db.event(self.name, f"[{d}] my rejections are adding value again - veto restored")
