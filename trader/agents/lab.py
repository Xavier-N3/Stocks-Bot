"""Training lab: runs neuroevolution for every desk. It evolves after each
closed trade (on request from the traders/manager), and at least once a day,
continuing from the saved population so the brains keep improving over time."""
import json
import time
import uuid
from datetime import datetime, timedelta, timezone

import numpy as np

from .. import brain, config, db, evolution, features, risk, universe
from ..broker import Broker
from .base import Agent


class Lab(Agent):
    name = "lab"
    title = "Training lab (neuroevolution)"

    def __init__(self):
        super().__init__()
        self.broker = Broker()
        self.cache = {}
        self.rushing = False
        self._pool = None          # (dataset, worker pool) kept warm for crypto boost runs

    def wait_seconds(self):
        return 5 if self.rushing else 60

    def _rush(self):
        """True until a crypto brain passes the strict backtest bar (then it moves on to shadow testing)."""
        if not config.TRAINING_RUSH or not risk.crypto_enabled():
            return False
        from .. import crypto_shadow
        return crypto_shadow.stage(brain.champion("crypto"))["stage"] == "training"

    def step(self):
        rush = self._rush()
        if rush != self.rushing:
            db.event(self.name, ("Crypto boost ON: crypto trains nonstop with bigger, longer runs, other desks every "
                                 f"{config.RUSH_OTHERS_EVERY_MIN} min, until a crypto brain passes") if rush else
                                "A crypto brain passed the backtest bar - boost OFF, back to the normal training pace")
            self.rushing = rush
            if not rush:
                self._close_pool()
        for name in config.DESKS:
            if self._data_ready(name) and self._due(name):
                self._evolve(name)

    def _crypto_pool(self, ds):
        if self._pool and self._pool[0] is ds:
            return self._pool[1]
        self._close_pool()
        try:
            self._pool = (ds, evolution.make_pool(ds, config.DESKS["crypto"], config.LAB_WORKERS))
        except Exception as e:
            self.log.warning("could not start the crypto worker pool: %s", e)
            return None
        return self._pool[1]

    def _close_pool(self):
        if self._pool:
            self._pool[1].shutdown(wait=False, cancel_futures=True)
            self._pool = None

    def _data_ready(self, name):
        if name == "crypto" and not risk.crypto_enabled():
            return False                   # switched off in the Studio: don't spend CPU on it
        desk = config.DESKS[name]
        tf, need = {"day": ("15Min", 26 * 20), "crypto": ("1Hour", 24 * 60)}.get(name, ("1Day", 400))
        r = db.one("SELECT COUNT(*) n FROM bars WHERE symbol=? AND tf=?", (desk.bench, tf))
        return r["n"] >= need

    def _due(self, name):
        if brain.champion(name) is None:
            return True
        last = db.kv_get(f"last_evolve:{name}")
        if not last:
            return True
        age = datetime.now(timezone.utc) - datetime.fromisoformat(last)
        if self.rushing:
            return name == "crypto" or age >= timedelta(minutes=config.RUSH_OTHERS_EVERY_MIN)
        if name == "crypto" and brain.status(brain.champion("crypto")) != "active":
            return age >= timedelta(minutes=config.CRYPTO_TRAIN_EVERY_MIN)   # keep training until good enough
        req = db.kv_get(f"evolve_request:{name}")
        if req and req > last and age >= timedelta(minutes=config.DESKS[name].evolve_cooldown_min):
            return True
        return age >= timedelta(hours=20)

    def _symbols(self, name):
        desk = config.DESKS[name]
        if name == "crypto":
            return sorted(set(config.CRYPTO_UNIVERSE) | {desk.bench})
        if name == "day":
            rows = db.query("SELECT symbol, COUNT(*) n FROM bars WHERE tf='15Min' GROUP BY symbol HAVING n >= ? "
                            "ORDER BY n DESC", (26 * 15,))
            syms = [r["symbol"] for r in rows if r["symbol"] not in config.EXCLUDE_SYMBOLS]
        else:
            # ALL stocks, not "the most traded lately": stocks that just rallied are the most
            # traded, so picking by recent volume would quietly hand the lab the winners.
            rows = db.query("SELECT DISTINCT symbol FROM bars WHERE tf='1Day' ORDER BY symbol")
            syms = [r["symbol"] for r in rows if r["symbol"] not in config.EXCLUDE_SYMBOLS]
        syms = syms[:desk.train_symbols]
        return sorted(set(syms) | {"SPY"})

    def _dataset(self, name):
        desk = config.DESKS[name]
        max_age = 3 * 3600 if name == "day" else 12 * 3600
        hit = self.cache.get(name)
        if hit and time.time() - hit[0] < max_age:
            return hit[1]
        since = (datetime.now(timezone.utc) - timedelta(days=desk.history_days)).isoformat()
        bars = db.load_bars(self._symbols(name), desk.timeframe, since)
        norm = db.kv_get(f"norm:{name}")
        start_dates = None
        if name in ("swing", "long"):   # survivorship guard: only count a stock from the day it joined the S&P 500
            start_dates = {s: v["added"] for s, v in universe.sp500().items() if v.get("added")}
        ds = features.build_dataset(desk, bars, norm=norm, start_dates=start_dates)
        if ds is None:
            return None
        if norm is None:      # freeze feature scaling so every generation's brains stay comparable
            db.kv_set(f"norm:{name}", [ds["mu"].tolist(), ds["sd"].tolist()])
        self.cache[name] = (time.time(), ds)
        self.log.info("[%s] training data: %d samples, %d symbols, %d bars (holdout = last %d bars)",
                      name, len(ds["X"]), len(ds["symbols"]), ds["n_dates"], ds["n_dates"] - ds["split"])
        return ds

    def _evolve(self, name):
        desk = config.DESKS[name]
        ds = self._dataset(name)
        if ds is None:
            return
        champ = brain.champion(name)
        first = champ is None
        market_open = self.broker.clock()["is_open"]
        boost = name == "crypto" and self.rushing and config.CRYPTO_BOOST
        budget = 300 if first or boost else (45 if name == "day" and market_open else 150)
        gens = config.BOOST_GENS if boost else 40 if first else (40 if name == "crypto" else 15)
        pop_size = config.BOOST_POP if boost else 64 if name == "crypto" else 32
        pop = db.kv_get(f"population:{name}")
        if boost and pop and (db.kv_get("crypto_stall") or 0) >= config.BOOST_STALL_RUNS:
            pop = pop[:8]          # search is stuck: keep the 8 best brains, fill the rest with fresh random ones
            db.kv_set("crypto_stall", 0)
            db.event(self.name, f"[crypto] no progress in {config.BOOST_STALL_RUNS} runs - shaking up the gene pool "
                                "(keeping the 8 best brains, the rest are brand new)")
        self.activity(f"Breeding {pop_size} {name} brains on {config.LAB_WORKERS} CPU cores, up to {gens} generations "
                      f"on {len(ds['X']):,} historical setups{' (crypto boost)' if boost else ''}")
        t0 = time.time()
        try:
            pop, cands, ran = evolution.evolve(ds, desk, pop, gens, pop_size, budget, workers=config.LAB_WORKERS,
                                               pool=self._crypto_pool(ds) if boost else None,
                                               n_cands=config.BOOST_CANDIDATES if boost else 6)
        except Exception:
            if boost:
                self._close_pool()     # a broken worker pool gets rebuilt next run
            raise
        db.kv_set(f"population:{name}", [np.round(v, 5).tolist() for v in pop])
        best = cands[0]
        champ_h = None
        if champ is not None and len(champ["vec"]) == evolution.n_genes(len(ds["feats"])):
            champ_h = evolution.simulate(champ["vec"], ds, desk, "holdout")
        margin = 0.05
        if name == "crypto" and champ is not None:
            # don't churn: a brain in (or past) its shadow test is only replaced by a clearly better one
            from .. import crypto_shadow
            st = crypto_shadow.stage(champ)
            margin = 0.5 if st["stage"] in ("shadow", "cleared") and st["shadow"]["trades"] >= 3 else 0.25
        promote = champ_h is None or (best["holdout"]["fitness"] > champ_h["fitness"] + margin
                                      and best["holdout"]["trades"] >= 5)
        if name == "crypto":
            # a brain that clears the full backtest bar always replaces a champion that doesn't
            from .. import crypto_shadow
            passing = [c for c in cands if crypto_shadow.backtest_ok(c)]
            if passing and not (champ and crypto_shadow.backtest_ok(champ)):
                best, promote = passing[0], True
        if boost:
            db.kv_set("crypto_stall", 0 if promote else (db.kv_get("crypto_stall") or 0) + 1)
        note = ""
        if promote:
            gid = f"{name}-{datetime.now(timezone.utc):%Y%m%d%H%M}-{uuid.uuid4().hex[:4]}"
            db.execute("UPDATE genomes SET role='retired' WHERE desk=? AND role='champion'", (name,))
            data = {"vec": np.round(best["vec"], 6).tolist(), "feats": ds["feats"],
                    "mu": ds["mu"].tolist(), "sd": ds["sd"].tolist(), "train": best["train"],
                    "holdout": best["holdout"], "holdout_dates": [ds["dates"][ds["split"]], ds["dates"][-1]],
                    "params": {k: v for k, v in evolution.decode(best["vec"], len(ds["feats"]), desk).items()
                               if k in ("thr", "stop", "target", "hold")}}
            db.execute("INSERT INTO genomes VALUES (?,?,?,?,?,?,?,?)",
                       (gid, name, db.now_iso(), "champion", best["train"]["fitness"], best["holdout"]["fitness"],
                        best["holdout"]["trades"], json.dumps(data)))
            note = (f"new champion {gid}: unseen-data sharpe {best['holdout']['sharpe']}, "
                    f"return {best['holdout']['total'] * 100:+.1f}% over {best['holdout']['trades']} trades "
                    f"(old champion fitness {champ_h['fitness'] if champ_h else 'n/a'})")
            db.event(self.name, f"[{name}] {note}")
        else:
            db.execute("UPDATE genomes SET holdout_fit=? WHERE id=?", (champ_h["fitness"], champ["id"]))
            note = (f"champion kept (its unseen-data fitness {champ_h['fitness']:.3f} vs best challenger "
                    f"{best['holdout']['fitness']:.3f})")
        db.execute("INSERT INTO evolution_log (desk, ts, generations, best_train, best_holdout, champion_holdout, "
                   "promoted, note) VALUES (?,?,?,?,?,?,?,?)",
                   (name, db.now_iso(), ran, best["train"]["fitness"], best["holdout"]["fitness"],
                    champ_h["fitness"] if champ_h else None, int(promote), note))
        db.kv_set(f"last_evolve:{name}", db.now_iso())
        self.log.info("[%s] evolved %d generations in %.0fs - %s", name, ran, time.time() - t0, note)
        self.activity(f"{name}: {'NEW CHAMPION' if promote else 'champion kept'} after {ran} generations")
