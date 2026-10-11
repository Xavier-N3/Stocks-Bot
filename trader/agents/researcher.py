"""Researcher agents (one per desk). They keep the price-history database
current, rank stocks with the desk's evolved brain, read news from Alpaca and
the web, ask an AI analyst to judge each stock, and hand BRIEFS to their trader."""
import hashlib
import json
import time
from datetime import datetime, timedelta, timezone

from .. import brain, config, db, features, library, llm, risk, universe, web
from ..broker import Broker
from ..marketdata import MarketData
from ..timeutil import ET, et_minutes, now_et
from .base import Agent

HORIZON = {"day": "the rest of today's trading session", "swing": "the next 1-4 weeks",
           "long": "the next 6-12 months", "crypto": "the next 1-48 hours", "forex": "the next 2-24 hours"}
TOP_N = {"day": 12, "swing": 15, "long": 25, "crypto": 13, "forex": 12}
BRIEF_TTL_MIN = {"day": 45, "swing": 20 * 60, "long": 7 * 24 * 60, "crypto": 120, "forex": 120}
REANALYZE_S = {"day": 3600, "swing": 6 * 3600, "long": 24 * 3600, "crypto": 3 * 3600, "forex": 3 * 3600}
NEWS_HOURS = {"day": 20, "swing": 72, "long": 168, "crypto": 24, "forex": 24}
CURRENCY_NAMES = {"EUR": "euro", "USD": "US dollar", "GBP": "British pound", "JPY": "Japanese yen",
                  "AUD": "Australian dollar", "CAD": "Canadian dollar", "CHF": "Swiss franc", "NZD": "New Zealand dollar"}


def fx_label(sym) -> str:
    """'EUR_USD_SHORT' -> 'EUR/USD short (bets the euro falls vs the US dollar)'."""
    short = sym.endswith("_SHORT")
    base, quote = sym.replace("_SHORT", "").split("_")
    b, q = CURRENCY_NAMES.get(base, base), CURRENCY_NAMES.get(quote, quote)
    return f"{base}/{quote} {'short' if short else 'long'} (bets the {b} {'falls' if short else 'rises'} vs the {q})"
COIN_NAMES = {"BTC": "Bitcoin", "ETH": "Ethereum", "SOL": "Solana", "XRP": "XRP Ripple", "DOGE": "Dogecoin",
              "LTC": "Litecoin", "LINK": "Chainlink", "AVAX": "Avalanche", "ADA": "Cardano", "DOT": "Polkadot",
              "BCH": "Bitcoin Cash", "UNI": "Uniswap", "AAVE": "Aave"}


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
        self._fx = None
        self._wait = 60

    def wait_seconds(self):
        return self._wait

    def step(self):
        if self.desk.name == "crypto":
            return self._crypto()
        if self.desk.name == "forex":
            return self._forex()
        clock = self.broker.clock()
        if self.desk.name == "day":
            self._day(clock)
        else:
            self._daily(clock)

    # ------------------------------------------------------------ crypto desk (24/7)
    def _crypto(self):
        if not risk.crypto_enabled():
            self._wait = 120
            self.activity("Crypto desk is switched off (turn it on in the Studio)")
            return
        self._wait = 600
        uni = config.CRYPTO_UNIVERSE
        self.activity(f"Updating hourly prices for {len(uni)} coins")
        self.md.update_crypto(uni)
        market_context(90, self.name)
        fdf = features.live_features(self.desk, uni)
        self._research(self._rank(fdf), True)

    # ------------------------------------------------------------ forex desk (24/5, OANDA)
    def _forex(self):
        from .. import oanda
        if not config.FOREX_ENABLED:
            self._wait = 1800
            self.activity("Waiting for a free OANDA practice account (add OANDA_API_TOKEN to .env)")
            return
        self._wait = 600
        if self._fx is None:
            self._fx = oanda.Oanda()
        self.activity(f"Updating hourly prices for {len(config.FOREX_PAIRS)} currency pairs")
        oanda.update_forex(self._fx)
        if not oanda.market_open():
            self._wait = 1800
            self.activity("Forex market is closed for the weekend (reopens Sunday 5pm New York time)")
            return
        market_context(90, self.name)
        fdf = features.live_features(self.desk, config.FOREX_UNIVERSE)
        self._research(self._rank(fdf), True)

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
            elif self.desk.name == "crypto":
                scored["quant"] = scored["r168"].fillna(0) + scored["d_ema168"].fillna(0)
            elif self.desk.name == "forex":
                scored["quant"] = scored["r120"].fillna(0) + scored["d_ema120"].fillna(0)
            else:
                scored["quant"] = scored["r60"].fillna(0) + scored["d_sma200"].fillna(0)
            scored = scored.sort_values("quant", ascending=False)
        out = []
        for sym, r in scored.head(TOP_N[self.desk.name]).iterrows():
            nums = {"price": round(float(r["c"]), 6 if self.desk.name == "forex" else 2)}
            keys = (["day_ret", "gap", "d_vwap", "vratio", "rsi"] if self.desk.name == "day"
                    else ["r1", "r4", "r24", "r168", "d_ema168", "d_ema720", "rsi", "atr", "vol_ratio", "rs24", "rs168"]
                    if self.desk.name == "crypto"
                    else ["r1", "r4", "r24", "r120", "d_ema120", "d_ema480", "rsi", "atr", "vol_ratio", "usd_r24"]
                    if self.desk.name == "forex"
                    else ["r5", "r20", "r60", "mom12_1", "d_sma200", "d_hi252", "rsi", "atr"])
            for k in keys:
                v = r.get(k)
                if v == v and v is not None:
                    nums[k] = round(float(v), 4)
            if self.desk.name == "crypto":
                nums.update(self._crypto_context(sym))
            out.append((sym, float(r["quant"]), nums))
        return out

    def _crypto_context(self, sym):
        """Positioning & mood for the AI analyst (not brain inputs): perp funding rate and Fear & Greed."""
        from .. import macro
        out = {}
        rate = macro.funding_now().get(sym.split("/")[0])
        if rate is not None:
            out["funding_apr_pct"] = round(rate * 24 * 365 * 100, 1)   # hourly rate -> yearly %, + = longs crowded
        fng = macro.latest_fng()
        if fng is not None:
            out["fear_greed_0_100"] = int(fng)
        return out

    def _research(self, ranked, live):
        if not ranked:
            return
        d = self.desk.name
        syms = [s for s, _, _ in ranked]
        self.activity(f"Reading news on {', '.join(syms[:6])}{'...' if len(syms) > 6 else ''}")
        fx = d == "forex"
        news_sym = (lambda s: s.replace("_SHORT", "")) if fx else (lambda s: s)   # a pair's long & short share news
        if not fx:                                  # Alpaca's news feed covers stocks and crypto, not currencies
            self.md.fetch_news(syms, NEWS_HOURS[d])
        web_every = 1800 if d in ("day", "crypto") else 6 * 3600
        for s in (sorted({news_sym(x) for x in syms}) if fx else syms[:8]):
            if time.time() - self.last_web.get(s, 0) > web_every:
                if fx:
                    web.store_web_news(s, f"{s.replace('_', '/')} forex outlook")
                elif "/" in s:     # crypto: search the coin's name, not "BTC/USD stock"
                    coin = COIN_NAMES.get(s.split("/")[0], s.split("/")[0])
                    web.store_web_news(s, f"{coin} crypto price")
                else:
                    web.store_web_news(s, universe.info(s).get("name", ""))
                self.last_web[s] = time.time()
        since = (datetime.now(timezone.utc) - timedelta(hours=NEWS_HOURS[d])).isoformat()
        todo, packs = [], {}
        for sym, q, nums in ranked:
            heads = db.query("SELECT ts, source, headline FROM news WHERE symbol=? AND ts>=? ORDER BY ts DESC LIMIT 6",
                             (news_sym(sym), since))
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
            info = {"name": fx_label(s), "sector": "currencies"} if d == "forex" else universe.info(s)
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
                "Numbers: returns are fractions (0.02 = +2%); rsi is scaled -1..1; d_* = distance from that level."
                + (" Crypto: rN = return over the last N hours; rs* = vs Bitcoin; funding_apr_pct = perpetual-futures "
                   "funding (large + means crowded longs, a squeeze risk; - means crowded shorts); fear_greed_0_100 "
                   "= market mood (extremes often mean-revert)." if d == "crypto" else "")
                + (" Forex: each pair appears as a long and a SHORT twin (the short's price is flipped, so its "
                   "returns are the reverse); judge each twin on its own direction. rN = return over N hours; "
                   "usd_r24 = US dollar strength over 24h. Watch for central-bank meetings, CPI/jobs data and "
                   "intervention risk inside the holding period." if d == "forex" else "") + "\n\n"
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
