"""Manager agent: reviews every closed trade (success or failure, grade, what
went right/wrong), turns reviews into lessons the traders read, measures
whether the AI vetoes actually help, cuts size for desks that underperform
their backtests, shifts capital between desks, and writes the 8-hour report."""
import json
from datetime import datetime, timedelta, timezone

import numpy as np

from .. import brain, config, db, library, llm, report, risk
from ..broker import Broker
from ..marketdata import MarketData
from ..timeutil import ET, hhmm, now_et
from .base import Agent

FOLLOWUP_HOURS = {"day": 6, "swing": 24 * 7, "long": 24 * 30, "crypto": 24}


def grade(r_mult):
    if r_mult >= 1.5:
        return "A"
    if r_mult >= 0.5:
        return "B"
    if r_mult >= -0.25:
        return "C"
    if r_mult >= -1.0:
        return "D"
    return "F"


class Manager(Agent):
    name = "manager"
    title = "Manager"

    def __init__(self):
        super().__init__()
        self.broker = Broker()
        self.md = MarketData()
        self._last = {}

    def wait_seconds(self):
        return 60

    def _every(self, key, seconds):
        t = datetime.now(timezone.utc).timestamp()
        if t - self._last.get(key, 0) >= seconds:
            self._last[key] = t
            return True
        return False

    def step(self):
        self.activity("Watching the whole firm")
        self._review_closed()
        if self._every("equity", 900):
            self._snapshot_equity()
        if self._every("followup", 3600):
            self._followup_decisions()
            self._ai_value_check()
        if self._every("reality", 1800):
            for d in config.DESKS:
                self._reality_check(d)
        self._maybe_report()
        if self._every("sleeves", 6 * 3600):
            self._weekly_sleeves()
        if self._every("prune", 24 * 3600):
            db.execute("DELETE FROM briefs WHERE created < ?", ((datetime.now(timezone.utc) - timedelta(days=30)).isoformat(),))
            db.execute("DELETE FROM news WHERE ts < ?", ((datetime.now(timezone.utc) - timedelta(days=120)).isoformat(),))
            db.execute("DELETE FROM events WHERE ts < ?", ((datetime.now(timezone.utc) - timedelta(days=60)).isoformat(),))

    # ------------------------------------------------------------ trade reviews
    def _review_closed(self):
        for p in db.query("SELECT * FROM positions WHERE status='closed' AND reviewed=0 ORDER BY exit_time LIMIT 10"):
            desk = config.DESKS[p["desk"]]
            tf = {"day": "15Min", "crypto": "1Hour"}.get(p["desk"], "1Day")
            entry_t = datetime.fromisoformat(p["entry_time"])
            exit_t = datetime.fromisoformat(p["exit_time"])
            bench = config.DESKS[p["desk"]].bench
            bars = db.query("SELECT symbol, h, l, o, c FROM bars WHERE tf=? AND symbol IN (?, ?) AND ts >= ? AND ts <= ? "
                            "ORDER BY ts", (tf, p["symbol"], bench, (entry_t - timedelta(days=1 if tf == '1Day' else 0)).isoformat(),
                                            exit_t.isoformat()))
            mine = [b for b in bars if b["symbol"] == p["symbol"]]
            spy = [b for b in bars if b["symbol"] == bench]
            ep = p["entry_price"] or 0
            mfe = max(b["h"] for b in mine) / ep - 1 if mine and ep else None
            mae = min(b["l"] for b in mine) / ep - 1 if mine and ep else None
            spy_ret = spy[-1]["c"] / spy[0]["o"] - 1 if len(spy) >= 1 and spy[0]["o"] else None
            stop = p["stop_pct"] or desk.min_stop
            r_mult = (p["pnl_pct"] or 0) / stop
            g = grade(r_mult)
            hold_h = (exit_t - entry_t).total_seconds() / 3600
            review = {"r_mult": round(r_mult, 2), "mfe": mfe, "mae": mae, "spy_ret": spy_ret,
                      "beat_spy": None if spy_ret is None else (p["pnl_pct"] or 0) > spy_ret,
                      "hold_hours": round(hold_h, 1), "success": (p["pnl"] or 0) > 0}
            self.activity(f"Reviewing {p['desk']} trade in {p['symbol']} ({100 * (p['pnl_pct'] or 0):+.2f}%)")
            lesson = self._lesson(p, review, g)
            review["lesson"] = lesson
            db.execute("UPDATE positions SET reviewed=1, grade=?, review=? WHERE id=?", (g, json.dumps(review), p["id"]))
            if lesson and p["genome_id"] != "legacy":
                db.execute("INSERT INTO lessons (desk, ts, position_id, grade, text) VALUES (?,?,?,?,?)",
                           (p["desk"], db.now_iso(), p["id"], g, lesson))
            db.kv_set(f"evolve_request:{p['desk']}", db.now_iso())
            library.write(p["symbol"])
            self.log.info("[%s] reviewed %s: %+.2f%% grade %s (%s)", p["desk"], p["symbol"],
                          100 * (p["pnl_pct"] or 0), g, "success" if review["success"] else "failure")

    def _lesson(self, p, review, g):
        llm_view = json.loads(p["llm_json"] or "null") or {}
        feats = json.loads(p["features"] or "null") or {}
        user = (f"Desk: {config.DESKS[p['desk']].title}. Trade: bought {p['symbol']} at {p['entry_price']:.2f}, "
                f"sold at {p['exit_price']:.2f} ({100 * (p['pnl_pct'] or 0):+.2f}%, {review['r_mult']:+.2f}R, grade {g}). "
                f"Exit reason: {p['exit_reason']}. Held {review['hold_hours']}h. "
                f"Best point during trade {100 * (review['mfe'] or 0):+.2f}%, worst {100 * (review['mae'] or 0):+.2f}%. "
                f"S&P 500 over the same time: {100 * (review['spy_ret'] or 0):+.2f}%.\n"
                f"Research view at entry: {llm_view.get('summary') or 'n/a'} (sentiment {llm_view.get('sentiment')})\n"
                f"AI manager verdict at entry: {(llm_view.get('verdict') or {}).get('reason', 'n/a')}\n"
                f"Quant features at entry: {json.dumps(feats)[:800]}\n\n"
                "In ONE or TWO sentences, write a concrete, reusable lesson for this desk (what to repeat or avoid "
                'next time). JSON: {"verdict": "success" or "failure", "lesson": "..."}')
        res = llm.chat("You are a trading-desk manager doing post-trade reviews. Be specific and practical, not generic.",
                       user, tier="smart", max_tokens=250, agent=self.name)
        if res and isinstance(res.get("json"), dict) and res["json"].get("lesson"):
            return str(res["json"]["lesson"])[:400]
        # no AI available: a rule-based lesson
        mfe = review["mfe"] or 0
        if (p["pnl_pct"] or 0) < 0 and mfe > 2 * (p["stop_pct"] or 0.01):
            return f"{p['symbol']}: was up {mfe * 100:.1f}% before turning into a loss - the target may be too far for this setup."
        if p["exit_reason"] == "stop" and (review["mae"] or 0) < -2 * (p["stop_pct"] or 0.01):
            return f"{p['symbol']}: price gapped through the stop - be wary of overnight/news risk in similar setups."
        if review["success"]:
            return f"{p['symbol']}: setup worked ({p['exit_reason']}, {review['r_mult']:+.1f}R)."
        return f"{p['symbol']}: setup failed ({p['exit_reason']}, {review['r_mult']:+.1f}R)."

    # ------------------------------------------------------------ learning signals
    def _reality_check(self, d):
        """If live trades fall well short of what the backtest promised, halve
        the desk's size and ask the lab for a new brain. Restore when it recovers."""
        rows = db.query("SELECT pnl_pct FROM positions WHERE desk=? AND status='closed' AND genome_id != 'legacy' "
                        "ORDER BY exit_time DESC LIMIT 20", (d,))
        if len(rows) < 10:
            return
        rets = np.array([r["pnl_pct"] or 0 for r in rows])
        champ = brain.champion(d)
        expected = (champ or {}).get("holdout", {}).get("avg", 0)
        cur = risk.size_mult(d)
        if rets.mean() < 0 and rets.mean() < expected - 0.002:
            if cur != 0.5:
                db.kv_set(f"size_mult:{d}", 0.5)
                db.kv_set(f"evolve_request:{d}", db.now_iso())
                db.event(self.name, f"[{d}] live results ({rets.mean() * 100:+.2f}%/trade) are below backtest "
                                    f"({expected * 100:+.2f}%/trade): size halved, retraining requested", "warning")
        elif rets.mean() > 0 and cur != 1.0:
            db.kv_set(f"size_mult:{d}", 1.0)
            db.event(self.name, f"[{d}] live results recovered ({rets.mean() * 100:+.2f}%/trade): full size restored")

    def _followup_decisions(self):
        """Look up what happened to stocks the traders bought or the AI vetoed."""
        todo = []
        for d, hrs in FOLLOWUP_HOURS.items():
            cutoff = (datetime.now(timezone.utc) - timedelta(hours=hrs)).isoformat()
            todo += db.query("SELECT id, symbol, price FROM decisions WHERE desk=? AND later_price IS NULL AND ts < ? "
                             "AND action IN ('buy','veto','challenged') LIMIT 200", (d, cutoff))
        if not todo:
            return
        snaps = self.md.snapshots(sorted({t["symbol"] for t in todo}))
        for t in todo:
            px = (snaps.get(t["symbol"]) or {}).get("price")
            if px and t["price"]:
                db.execute("UPDATE decisions SET later_price=?, later_ret=? WHERE id=?",
                           (px, px / t["price"] - 1, t["id"]))

    def _ai_value_check(self):
        """If stocks the AI vetoed keep doing BETTER than the ones it approved,
        the AI is hurting: demote it to advisory for that desk."""
        if config.LLM_TRADE_MODE != "veto":
            return
        for d in config.DESKS:
            s = {r["action"]: r for r in db.query(
                "SELECT action, COUNT(*) n, AVG(later_ret) a FROM decisions WHERE desk=? AND later_ret IS NOT NULL "
                "GROUP BY action", (d,))}
            v, b = s.get("veto"), s.get("buy")
            if not v or not b or v["n"] < 30 or b["n"] < 30:
                continue
            mode = db.kv_get(f"llm_mode:{d}") or "veto"
            if v["a"] > b["a"] + 0.002 and mode == "veto":
                db.kv_set(f"llm_mode:{d}", "advisory")
                db.event(self.name, f"[{d}] AI vetoes were wrong on average (vetoed {v['a'] * 100:+.2f}% vs bought "
                                    f"{b['a'] * 100:+.2f}%): AI demoted to advisory for this desk", "warning")
            elif v["a"] < b["a"] - 0.002 and mode == "advisory":
                db.kv_set(f"llm_mode:{d}", "veto")
                db.event(self.name, f"[{d}] AI vetoes now add value: veto power restored")

    def _weekly_sleeves(self):
        """Once a week, move 5% of capital from the weakest desk to the strongest
        (only with enough live trades to judge, and within each desk's limits)."""
        if not config.AUTO_REALLOCATE:
            return
        week = now_et().strftime("%G-W%V")
        if now_et().weekday() != 6 or db.kv_get("sleeves_week") == week:
            return
        db.kv_set("sleeves_week", week)
        sl = risk.sleeves()
        since = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        perf = {}
        acct = self.broker.account()
        for d in config.DESKS:
            r = db.one("SELECT COUNT(*) n, SUM(pnl) p FROM positions WHERE desk=? AND status='closed' AND exit_time>=? "
                       "AND genome_id != 'legacy'", (d, since))
            if (r["n"] or 0) >= 15:
                perf[d] = (r["p"] or 0) / (acct["equity"] * sl[d])
        if len(perf) < 2:
            return
        best, worst = max(perf, key=perf.get), min(perf, key=perf.get)
        if perf[best] - perf[worst] < 0.01:
            return
        move = min(0.05, sl[worst] - config.DESKS[worst].sleeve_min, config.DESKS[best].sleeve_max - sl[best])
        if move > 0:
            sl[worst] = round(sl[worst] - move, 3)
            sl[best] = round(sl[best] + move, 3)
            db.kv_set("sleeves", sl)
            db.event(self.name, f"capital shifted {move * 100:.0f}% from {worst} to {best} desk (30-day returns on "
                                f"capital {perf[worst] * 100:+.1f}% vs {perf[best] * 100:+.1f}%)")

    # ------------------------------------------------------------ equity + reports
    def _snapshot_equity(self):
        a = self.broker.account()
        spy = (self.md.snapshots(["SPY"]).get("SPY") or {}).get("price")
        db.execute("INSERT OR REPLACE INTO equity VALUES (?,?,?,?)", (db.now_iso(), a["equity"], a["cash"], spy))
        mode = "restricted" if risk.pdt_restricted(a) else "unlocked"
        prev = db.kv_get("pdt_mode")
        if prev and prev != mode:
            db.event(self.name, "Account is above $25k: day-trade limit lifted, all desks unlocked to full position counts"
                     if mode == "unlocked" else "Account fell below $25k: back to 3 day trades per 5 days and fewer, "
                     "bigger positions", "info" if mode == "unlocked" else "warning")
        db.kv_set("pdt_mode", mode)
        if not db.kv_get("start"):
            db.kv_set("start", {"at": db.now_iso(), "equity": a["equity"], "spy": spy})

    def _maybe_report(self):
        n = now_et()
        slots = sorted(hhmm(t) for t in config.REPORT_TIMES_ET.split(",") if t.strip())
        mins = n.hour * 60 + n.minute
        past = [s for s in slots if s <= mins]
        if past:
            slot = n.replace(hour=past[-1] // 60, minute=past[-1] % 60, second=0, microsecond=0)
        else:
            slot = (n - timedelta(days=1)).replace(hour=slots[-1] // 60, minute=slots[-1] % 60, second=0, microsecond=0)
        key = slot.strftime("%Y-%m-%d %H:%M")
        if db.kv_get("last_report_slot") == key:
            return
        prev = db.kv_get("last_report_at")
        self.activity("Writing the 8-hour report")
        self._snapshot_equity()
        path = report.write(self.broker, since_iso=prev)
        library.write_index()
        db.kv_set("last_report_slot", key)
        db.kv_set("last_report_at", db.now_iso())
        self.log.info("report written: %s", path)
