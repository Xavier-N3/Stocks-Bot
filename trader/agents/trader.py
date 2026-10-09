"""Trader agents (one per desk). They read their researcher's briefs, score
each stock with the desk's evolved brain, ask an AI portfolio manager to
sanity-check the picks, size them under the risk rules, and place bracket
orders automatically. They also enforce holding-time exits."""
import json
from datetime import datetime, timedelta, timezone

import numpy as np

from .. import brain, config, db, features, llm, risk
from . import challenger
from ..broker import Broker
from ..marketdata import MarketData
from ..timeutil import ET, et_minutes, now_et
from .base import Agent

DECIDE_EVERY_S = {"day": 300, "swing": 1800, "long": 24 * 3600}


class Trader(Agent):
    def __init__(self, desk_name):
        self.name = f"{desk_name}_trader"
        self.title = f"{config.DESKS[desk_name].title} trader"
        super().__init__()
        self.desk = config.DESKS[desk_name]
        self.broker = Broker()
        self.md = MarketData()
        self._wait = 60
        self._last_block = ""

    def wait_seconds(self):
        return self._wait

    # ------------------------------------------------------------ main loop
    def step(self):
        d = self.desk.name
        closed = self.broker.sync(d)
        if closed:
            db.kv_set(f"evolve_request:{d}", db.now_iso())
        if d == "long":
            self._adopt_untracked()
        clock = self.broker.clock()
        n_open = len(self.broker.ledger(d))
        if not clock["is_open"]:
            self._wait = 600
            self.activity(f"Market closed - {n_open} position(s) guarded by stop-loss orders")
            return
        self._wait = 60 if d == "day" else 120
        m = et_minutes()
        if d == "day" and m >= 950:                       # 15:50 ET: day traders go home flat
            for p in self.broker.ledger(d, ("open",)):
                self.broker.close_position(p, "end of day")
            return
        if m >= 585:                                      # after 09:45 ET
            self._enforce_deadlines()
        last = db.kv_get(f"last_decide:{d}", 0)
        due = datetime.now(timezone.utc).timestamp() - last >= DECIDE_EVERY_S[d]
        window = {"day": (585, 915), "swing": (585, 930), "long": (600, 930)}[d]
        if not (due and window[0] <= m <= window[1]):
            self.activity(f"Watching {n_open} position(s)")
        if due and window[0] <= m <= window[1]:
            if d == "long":
                self._rebalance_long()
            self._decide()
            db.kv_set(f"last_decide:{d}", datetime.now(timezone.utc).timestamp())

    # ------------------------------------------------------------ exits
    def _enforce_deadlines(self):
        now = db.now_iso()
        for p in self.broker.ledger(self.desk.name, ("open",)):
            if p["exit_deadline"] and p["exit_deadline"] <= now:
                self.broker.close_position(p, "max holding time")

    def _deadline(self, hold_bars) -> str:
        d = self.desk.name
        if d == "day":
            end = now_et() + timedelta(minutes=15 * hold_bars)
            cap = now_et().replace(hour=15, minute=50, second=0, microsecond=0)
            return min(end, cap).astimezone(timezone.utc).isoformat()
        days = hold_bars * self.desk.path_step
        day = np.busday_offset(now_et().date().isoformat(), int(days), roll="forward")
        return datetime.fromisoformat(f"{day}T10:00:00").replace(tzinfo=ET).astimezone(timezone.utc).isoformat()

    # ------------------------------------------------------------ long desk housekeeping
    def _adopt_untracked(self):
        """Positions in the account that no desk owns (e.g. from the old bot)
        are handed to the long-term desk, which decides what to keep."""
        try:
            alp = self.broker.alpaca_positions()
        except Exception:
            return
        for sym, p in alp.items():
            if self.broker.owner(sym) is None and p["qty"] > 0:
                db.execute("INSERT INTO positions (desk, symbol, status, qty, entry_price, entry_time, stop_pct, "
                           "leg_ids, genome_id, score) VALUES ('long',?,?,?,?,?,?,?,?,?)",
                           (sym, "open", p["qty"], p["avg"], db.now_iso(), self.desk.min_stop, "[]", "legacy", 0))
                db.event(self.name, f"adopted untracked position {sym} x{p['qty']:.4g} into the long-term desk")
                self.log.info("adopted untracked position %s", sym)

    def _rebalance_long(self):
        """Weekly: sell adopted/legacy holdings the brain wouldn't buy, and trim
        the desk back inside its capital sleeve."""
        champ = brain.champion("long")
        if not champ:
            return
        held = self.broker.ledger("long", ("open",))
        if not held:
            return
        fdf = features.live_features(self.desk, [p["symbol"] for p in held])
        scored = brain.score_live("long", champ, fdf) if not fdf.empty else fdf
        score = {s: float(r["score"] - r["thr"]) for s, r in scored.iterrows()} if not scored.empty else {}
        acct = self.broker.account()
        alp = self.broker.alpaca_positions()
        cap = acct["equity"] * risk.sleeves()["long"]
        expo = risk.desk_exposure("long", {s: p["price"] for s, p in alp.items()})
        for p in sorted(held, key=lambda p: score.get(p["symbol"], -9)):
            value = p["qty"] * alp.get(p["symbol"], {}).get("price", p["entry_price"] or 0)
            legacy_reject = p["genome_id"] == "legacy" and score.get(p["symbol"], -9) < 0
            if expo > cap * 1.05 or legacy_reject:
                why = "rebalance: over capital sleeve" if expo > cap * 1.05 else "rebalance: brain would not buy this"
                self.broker.close_position(p, why)
                expo -= value

    # ------------------------------------------------------------ entries
    def _decide(self):
        d = self.desk.name
        champ = brain.champion(d)
        st = brain.status(champ)
        if st == "untrained":
            self._note("waiting for the training lab to evolve a first brain")
            return
        if st == "probation" and not config.PROBATION_TRADING:
            self._note("sitting out: no brain has proven an edge on unseen data yet")
            return
        acct = self.broker.account()
        alp = self.broker.alpaca_positions()
        ok, why = risk.can_buy(d, acct, alp)
        if not ok:
            self._note(why)
            return
        briefs = db.query("SELECT * FROM briefs WHERE desk=? AND expires > ? AND id IN "
                          "(SELECT MAX(id) FROM briefs WHERE desk=? GROUP BY symbol)", (d, db.now_iso(), d))
        brief = {b["symbol"]: b for b in briefs}
        # don't re-ask the AI about a stock it just vetoed
        cool = (datetime.now(timezone.utc) - timedelta(hours=1 if d == "day" else 24)).isoformat()
        vetoed = {r["symbol"] for r in db.query("SELECT symbol FROM decisions WHERE desk=? AND action IN ('veto','challenged') AND ts>=?",
                                                (d, cool))}
        syms = [s for s in brief if s not in vetoed and self.broker.owner(s) is None and not risk.symbol_blocked(d, s)]
        if not syms:
            self._note("no fresh research briefs to act on")
            return
        if d == "day":
            self.md.update_intraday(syms)
        fdf = features.live_features(self.desk, syms)
        if fdf.empty:
            return
        scored = brain.score_live(d, champ, fdf)
        cands = scored[scored["score"] > scored["thr"]]
        slots = risk.max_positions(d, acct) - len(self.broker.ledger(d))
        cands = cands.head(max(0, slots) * 2)
        if cands.empty:
            self._note("brain found no stock above its entry threshold")
            return
        snaps = self.md.snapshots(list(cands.index))
        picks = []
        for sym, r in cands.iterrows():
            price = (snaps.get(sym) or {}).get("price")
            if not price:
                continue
            picks.append({"symbol": sym, "price": price, "score": float(r["score"]), "thr": float(r["thr"]),
                          "stop_pct": float(r["stop_pct"]), "target_pct": float(r["target_pct"]),
                          "hold": int(r["hold"]), "brief": brief.get(sym, {}),
                          "features": {f: round(float(r[f]), 5) for f in champ["feats"]}})
        mode = db.kv_get(f"llm_mode:{d}") or config.LLM_TRADE_MODE   # manager may demote the AI to advisory
        self.activity(f"Asking the AI portfolio manager about {', '.join(p['symbol'] for p in picks)}")
        verdicts = self._ai_review(picks) if mode != "off" else {}
        # every trade the manager lets through goes to the Challenger for a pros/cons debate
        through = [dict(p, pm=verdicts.get(p["symbol"])) for p in picks
                   if not (verdicts.get(p["symbol"], {}).get("action") == "skip" and mode == "veto")]
        debates = challenger.review(d, through, self.name) if mode != "off" else {}
        mult = config.PROBATION_SIZE if st == "probation" else 1.0
        for p in picks:
            v = verdicts.get(p["symbol"], {})
            deb = debates.get(p["symbol"], {})
            action = v.get("action", "buy")
            size = float(v.get("size", 1.0) or 1.0)
            vetoed = action == "skip" and mode == "veto"
            challenged = not vetoed and deb.get("verdict") == "reject" and deb.get("mode") == "veto"
            margin = (p["score"] - p["thr"]) / (abs(p["thr"]) + 1)
            conviction = (1.0 + float(np.clip(margin, -0.5, 0.5))) * mult
            if mode == "veto":
                conviction *= max(0.5, min(size, config.PROFILE["conv_cap"]))
            if deb.get("mode") == "veto":
                conviction *= deb.get("size", 1.0)
            qty = 0 if vetoed or challenged else risk.position_qty(d, acct, alp, p["price"], p["stop_pct"], conviction)
            record = "veto" if vetoed else "challenged" if challenged else ("buy" if qty > 0 else "no_size")
            reason = v.get("reason", "") + (f" | Challenger: {deb.get('verdict')} - {deb.get('reasoning', '')}" if deb else "")
            db.execute("INSERT INTO decisions (desk, symbol, ts, action, score, llm_verdict, size_mult, reason, price) "
                       "VALUES (?,?,?,?,?,?,?,?,?)",
                       (d, p["symbol"], db.now_iso(), record, p["score"], action if v else None, size,
                        reason[:600], p["price"]))
            if qty <= 0:
                continue
            ok, why = risk.can_buy(d, acct, alp)
            if not ok:
                self._note(why)
                break
            try:
                self.broker.buy_bracket(d, p["symbol"], qty, p["price"], p["stop_pct"], p["target_pct"], {
                    "deadline": self._deadline(p["hold"]), "genome_id": champ["id"], "score": p["score"],
                    "features": p["features"],
                    "llm": {"verdict": v, "sentiment": p["brief"].get("sentiment"),
                            "summary": p["brief"].get("summary"), "debate": deb}})
                self.activity(f"Bought {qty} {p['symbol']} @ ${p['price']:.2f}")
                db.event(self.name, f"BUY {qty} {p['symbol']} @ ${p['price']:.2f}")
                acct = self.broker.account()
            except Exception as e:
                self.log.warning("order for %s failed: %s", p["symbol"], e)
                db.event(self.name, f"order for {p['symbol']} failed: {e}", "warning")

    def _ai_review(self, picks) -> dict:
        d = self.desk.name
        lessons = db.query("SELECT text, grade FROM lessons WHERE desk=? ORDER BY id DESC LIMIT 8", (d,))
        rec = db.one("SELECT COUNT(*) n, SUM(pnl>0) w, AVG(pnl_pct) a FROM positions WHERE desk=? AND status='closed'", (d,))
        ctx = db.kv_get("market_context") or {}
        lines = []
        for p in picks:
            b = p["brief"]
            sent = "n/a" if b.get("sentiment") is None else f"{b['sentiment']:+.2f} (conf {b.get('confidence') or 0:.2f})"
            lines.append(f"{p['symbol']} @ ${p['price']:.2f} | quant score {p['score']:+.3f} vs threshold {p['thr']:+.3f} | "
                         f"stop -{p['stop_pct'] * 100:.1f}% target +{p['target_pct'] * 100:.1f}% | research sentiment {sent}\n"
                         f"  summary: {b.get('summary') or '-'}\n  catalysts: {b.get('catalysts') or '-'}\n"
                         f"  risks: {b.get('risks') or '-'}")
        system = (f"You are the {self.desk.title.lower()} portfolio manager at a disciplined fund. An evolved "
                  "quantitative model, tested on historical data, has flagged BUY candidates. Your job is a sanity "
                  "check: veto a pick only for a concrete red flag in the research (earnings within the holding "
                  "period for short-term desks, fraud/lawsuit, dilutive offering, trading halt, guidance cut, "
                  "buyout pending, parabolic spike already extended). Normal uncertainty is NOT a reason to veto. "
                  "You may size between 0.5 (cautious) and 1.5 (high conviction). Reply with JSON only.")
        user = (f"Market backdrop: {ctx.get('summary', 'n/a')} (risk {ctx.get('risk', '?')}/5)\n"
                f"Desk record so far: {rec['n'] or 0} closed trades, {rec['w'] or 0} winners, "
                f"avg {100 * (rec['a'] or 0):+.2f}% per trade\n"
                "Lessons from past trades:\n" + ("\n".join(f"- [{l['grade']}] {l['text']}" for l in lessons) or "- none yet") +
                "\n\nCandidates:\n" + "\n".join(lines) +
                '\n\nJSON: {"decisions": [{"symbol": "X", "action": "buy" or "skip", "size": 1.0, "reason": "short"}]}')
        res = llm.chat(system, user, tier="smart", max_tokens=900, agent=self.name, cache_min=5)
        out = {}
        if res and isinstance(res.get("json"), dict):
            for it in res["json"].get("decisions", []) or []:
                sym = str(it.get("symbol", "")).upper()
                act = str(it.get("action", "buy")).lower()
                try:
                    size = float(it.get("size", 1.0))
                except (TypeError, ValueError):
                    size = 1.0
                out[sym] = {"action": "skip" if act.startswith("skip") else "buy", "size": size,
                            "reason": str(it.get("reason", ""))[:300], "model": f"{res['provider']}:{res['model']}"}
        return out

    def _note(self, msg):
        self.activity(msg[:1].upper() + msg[1:])
        if msg != self._last_block:
            self.log.info(msg)
            self._last_block = msg
