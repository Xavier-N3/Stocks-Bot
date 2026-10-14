"""Researcher agents (one per desk). They keep the price-history database
current, rank stocks with the desk's evolved brain, read news from Alpaca and
the web, ask an AI analyst to judge each stock, and hand BRIEFS to their trader."""
import hashlib
import json
import time
from datetime import datetime, timedelta, timezone

from .. import brain, config, db, features, library, llm, universe, web
from ..broker import Broker
from ..marketdata import MarketData
from ..timeutil import ET, et_minutes, now_et
from .base import Agent

HORIZON = {"day": "the rest of today's trading session", "swing": "the next 1-4 weeks",
           "long": "the next 6-12 months"}
TOP_N = {"day": 12, "swing": 15, "long": 25}
BRIEF_TTL_MIN = {"day": 45, "swing": 20 * 60, "long": 7 * 24 * 60}
REANALYZE_S = {"day": 3600, "swing": 6 * 3600, "long": 24 * 3600}
NEWS_HOURS = {"day": 20, "swing": 72, "long": 168}


def daily_is_stale() -> bool:
    last = db.kv_get("daily_updated")
    if not last:
        return True
    last = datetime.fromisoformat(last)
    if datetime.now(timezone.utc) - last > timedelta(hours=12):
        return True
    n = now_et()
    close_mark = n.replace(hour=16, minute=20, second=0, microsecond=0)
    return n.weekday() < 5 and n >= close_mark and last < close_mark.astimezone(timezone.utc)


def market_context(ttl_min=60, agent="researcher") -> dict:
    """Shared one-paragraph view of the overall market, refreshed at most hourly."""
    ctx = db.kv_get("market_context")
    if ctx and datetime.now(timezone.utc) - datetime.fromisoformat(ctx["at"]) < timedelta(minutes=ttl_min):
        return ctx
    heads = web.market_headlines()
    if not heads:
        return ctx or {"summary": "", "risk": 3}
    text = "\n".join(f"- {h['headline']} ({h['source']})" for h in heads)
    res = llm.chat("You are a markets strategist. Be concise and factual. Reply with JSON only.",
                   f"Today's headlines:\n{text}\n\nSummarize the US stock market backdrop in 2-3 short sentences "
                   'and rate risk 1 (calm) to 5 (very risky). JSON: {"summary": "...", "risk": 3}',
                   tier="fast", max_tokens=300, agent=agent)
    j = (res or {}).get("json") or {}
    ctx = {"at": db.now_iso(), "summary": j.get("summary") or "Headlines: " + "; ".join(h["headline"] for h in heads[:5]),
           "risk": j.get("risk", 3), "model": f"{res['provider']}:{res['model']}" if res else None}
    db.kv_set("market_context", ctx)
    return ctx


class Researcher(Agent):
    def __init__(self, desk_name):
        self.name = f"{desk_name}_researcher"
        self.title = f"{config.DESKS[desk_name].title} researcher"
        super().__init__()
        self.desk = config.DESKS[desk_name]
        self.md = MarketData()
        self.broker = Broker()
        self.seen = {}
        self.last_web = {}
        self.last_offhours = 0
        self._wait = 60

    def wait_seconds(self):
        return self._wait

    def step(self):
        clock = self.broker.clock()
        if self.desk.name == "day":
            self._day(clock)
        else:
            self._daily(clock)

    # ------------------------------------------------------------ day desk
    def _day_universe(self):
        trad = self.md.tradable()
        core = [s for s in config.DAY_CORE if s not in config.EXCLUDE_SYMBOLS]
        cand = {m["symbol"] for m in self.md.movers(25) if m["price"] >= 5} | set(self.md.most_actives(40))
        cand = {s for s in cand - set(core) if s in trad and s.isalpha() and s not in config.EXCLUDE_SYMBOLS}
        extra = []
        if cand:
            snaps = self.md.snapshots(sorted(cand))
            dv = {s: v["price"] * v["day_volume"] for s, v in snaps.items() if v["price"] >= 5}
            # IEX volume is a small slice of the whole market, so $1M on IEX ~ $30M+ overall
            extra = [s for s, _ in sorted(dv.items(), key=lambda x: -x[1]) if dv[s] >= 1e6][:20]
        uni = core + extra
        db.kv_set("day_universe", {"date": str(now_et().date()), "symbols": uni, "movers": extra})
        return uni

    def _day(self, clock):
        m = et_minutes()
        weekday = now_et().weekday() < 5
        live = clock["is_open"] or (weekday and 510 <= m < 570)      # session or 08:30-09:30 pre-market
        if live:
            self.activity("Scanning today's top movers and most-active stocks")
            uni = self._day_universe()
            self.md.update_intraday(uni)
            self._wait = 300
        else:
            self._wait = 900
            saved = db.kv_get("day_universe") or {}
            uni = saved.get("symbols") or config.DAY_CORE
            if time.time() - self.last_offhours < 3 * 3600:
                return
            self.last_offhours = time.time()
            self.md.update_intraday(sorted(set(config.DAY_CORE) - config.EXCLUDE_SYMBOLS))
        market_context(60 if live else 180, self.name)
        fdf = features.live_features(self.desk, uni, today_only=clock["is_open"])
        self._research(self._rank(fdf), live)

    # ------------------------------------------------------------ swing / long desks
    def _daily(self, clock):
        self._wait = 600
        uni = universe.broad(self.md.tradable())
        have = db.last_bar_ts("1Day")
        if daily_is_stale() or any(s not in have for s in uni):
            self.activity(f"Updating 6 years of daily prices for {len(uni)} stocks")
            n = self.md.update_daily(uni)
            self.log.info("daily history updated (%d bars)", n)
        if self.desk.name == "long":
            sp = universe.sp500()
            todo = {s: sp[s]["cik"] for s in uni if s in sp}
            self.activity("Reading SEC filings for company financials")
            got = web.update_fundamentals(todo, limit=100)   # ~100 companies per step until all are fresh
            if got:
                self.log.info("SEC fundamentals refreshed for %d companies", got)
        last = db.kv_get(f"last_brief:{self.desk.name}", 0)
        m = et_minutes()
        session = now_et().weekday() < 5 and 525 <= m < 960
        every = (3 * 3600 if session else 8 * 3600) if self.desk.name == "swing" else 20 * 3600
        if time.time() - last < every:
            self.activity(f"Watching {len(uni)} stocks - next deep research in "
                          f"{(every - (time.time() - last)) / 3600:.1f}h")
            return
        fdf = features.live_features(self.desk, uni)
        self._research(self._rank(fdf), session)
        db.kv_set(f"last_brief:{self.desk.name}", time.time())

    # ------------------------------------------------------------ shared
    def _rank(self, fdf):
        if fdf is None or fdf.empty:
            return []
        champ = brain.champion(self.desk.name)
        if champ:
            scored = brain.score_live(self.desk.name, champ, fdf)
            scored["quant"] = scored["score"] - scored["thr"]
        else:   # brain not trained yet: simple momentum/activity ranking
            scored = fdf.copy()
            if self.desk.name == "day":
                scored["quant"] = scored["day_ret"].abs().fillna(0) + scored["gap"].abs().fillna(0)
            else:
                scored["quant"] = scored["r60"].fillna(0) + scored["d_sma200"].fillna(0)
            scored = scored.sort_values("quant", ascending=False)
        out = []
        for sym, r in scored.head(TOP_N[self.desk.name]).iterrows():
            nums = {"price": round(float(r["c"]), 2)}
            keys = (["day_ret", "gap", "d_vwap", "vratio", "rsi"] if self.desk.name == "day"
                    else ["r5", "r20", "r60", "mom12_1", "d_sma200", "d_hi252", "rsi", "atr"])
            for k in keys:
                v = r.get(k)
                if v == v and v is not None:
                    nums[k] = round(float(v), 4)
            out.append((sym, float(r["quant"]), nums))
        return out

    def _research(self, ranked, live):
        if not ranked:
            return
        d = self.desk.name
        syms = [s for s, _, _ in ranked]
        self.activity(f"Reading news on {', '.join(syms[:6])}{'...' if len(syms) > 6 else ''}")
        self.md.fetch_news(syms, NEWS_HOURS[d])
        web_every = 1800 if d == "day" else 6 * 3600
        for s in syms[:8]:
            if time.time() - self.last_web.get(s, 0) > web_every:
                web.store_web_news(s, universe.info(s).get("name", ""))
                self.last_web[s] = time.time()
        since = (datetime.now(timezone.utc) - timedelta(hours=NEWS_HOURS[d])).isoformat()
        todo, packs = [], {}
        for sym, q, nums in ranked:
            heads = db.query("SELECT ts, source, headline FROM news WHERE symbol=? AND ts>=? ORDER BY ts DESC LIMIT 6",
                             (sym, since))
            h = hashlib.md5(json.dumps([x["headline"] for x in heads]).encode()).hexdigest()
            packs[sym] = (q, nums, heads)
            prev = self.seen.get(sym)
            if not (prev and prev["hash"] == h and time.time() - prev["t"] < REANALYZE_S[d]):
                todo.append(sym)
                self.seen[sym] = {"hash": h, "t": time.time(), "a": prev["a"] if prev else None}
        if todo:
            self.activity(f"AI analyst judging {len(todo)} stocks: {', '.join(todo[:5])}")
        for i in range(0, len(todo), 8):
            for sym, a in self._analyze(todo[i:i + 8], packs).items():
                self.seen[sym]["a"] = a
        expires = (datetime.now(timezone.utc) + timedelta(minutes=BRIEF_TTL_MIN[d])).isoformat()
        for sym, (q, nums, heads) in packs.items():
            a = (self.seen.get(sym) or {}).get("a") or {}
            db.execute("INSERT INTO briefs (desk, symbol, created, expires, quant_score, sentiment, confidence, "
                       "catalysts, risks, summary, model, data) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                       (d, sym, db.now_iso(), expires, q, a.get("sentiment"), a.get("confidence"),
                        a.get("catalysts"), a.get("risks"), a.get("summary"), a.get("model"),
                        json.dumps({"numbers": nums, "headlines": [x["headline"] for x in heads]})))
            library.write(sym, nums)
        self.log.info("briefed %d stocks for the %s trader (%d freshly analyzed by AI)", len(packs), d, len(todo))
        top = sorted(packs, key=lambda s: -packs[s][0])[:3]
        self.activity(f"Handed {len(packs)} briefs to the {d} trader (top: {', '.join(top)})")
        db.kv_set(f"handoff:{d}", {"at": db.now_iso(), "symbols": top})

    def _analyze(self, syms, packs) -> dict:
        d = self.desk.name
        ctx = db.kv_get("market_context") or {}
        blocks = []
        for s in syms:
            q, nums, heads = packs[s]
            info = universe.info(s)
            line = f"{s} ({info.get('name', '')}, {info.get('sector', '')}) quant_score={q:+.3f} " + \
                   " ".join(f"{k}={v}" for k, v in nums.items())
            if d == "long":
                f = web.get_fundamentals(s)
                if f:
                    line += "\n  fundamentals: " + ", ".join(f"{k}={v}" for k, v in f.items() if v is not None)
            hl = "\n".join(f"  - [{x['ts'][:16]} {x['source']}] {x['headline'][:160]}" for x in heads) or "  - (no recent news)"
            blocks.append(f"{line}\n{hl}")
        system = (f"You are the {self.desk.title.lower()} research analyst at a disciplined quant fund. You judge "
                  f"each stock's outlook over {HORIZON[d]} from its numbers and headlines. Be skeptical: most "
                  "headlines are noise or already priced in. Flag concrete risks (earnings date, lawsuits, share "
                  "offerings, guidance cuts, halts). Reply with JSON only.")
        user = (f"Market backdrop: {ctx.get('summary', 'n/a')} (risk {ctx.get('risk', '?')}/5)\n\n"
                "Numbers: returns are fractions (0.02 = +2%); rsi is scaled -1..1; d_* = distance from that level.\n\n"
                + "\n\n".join(blocks) +
                '\n\nFor EVERY stock return: {"stocks": [{"symbol": "X", "sentiment": -1..1, "confidence": 0..1, '
                '"catalysts": "short", "risks": "short", "summary": "one sentence"}]}')
        res = llm.chat(system, user, tier="fast", max_tokens=1600, agent=self.name)
        out = {}
        if res and isinstance(res.get("json"), dict):
            for it in res["json"].get("stocks", []) or []:
                sym = str(it.get("symbol", "")).upper()
                if sym in syms:
                    try:
                        sent = max(-1.0, min(1.0, float(it.get("sentiment", 0))))
                        conf = max(0.0, min(1.0, float(it.get("confidence", 0.5))))
                    except (TypeError, ValueError):
                        sent, conf = 0.0, 0.0
                    out[sym] = {"sentiment": sent, "confidence": conf,
                                "catalysts": str(it.get("catalysts", ""))[:300],
                                "risks": str(it.get("risks", ""))[:300],
                                "summary": str(it.get("summary", ""))[:400],
                                "model": f"{res['provider']}:{res['model']}"}
        return out
