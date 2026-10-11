"""Neuroevolution: each desk's trading brain is a small neural network plus
four trade settings (entry threshold, stop, target, holding time). A population
of brains is replayed over market history; the fittest breed and mutate. A new
brain only replaces the live one if it ALSO wins on recent data it never
trained on (the "holdout"), which is what keeps this from just memorizing the past."""
import heapq
from bisect import bisect_right
import math
import time

import numpy as np

HIDDEN = 8
N_PARAMS = 4   # threshold, stop, target, hold


def n_genes(n_feats):
    return n_feats * HIDDEN + HIDDEN + HIDDEN + 1 + N_PARAMS


def random_vec(n_feats, rng):
    v = rng.normal(0, 0.5, n_genes(n_feats))
    v[-N_PARAMS:] = rng.uniform(0, 1, N_PARAMS)
    return v


def decode(vec, n_feats, desk):
    F, Hd = n_feats, HIDDEN
    i = 0
    W1 = vec[i:i + F * Hd].reshape(F, Hd); i += F * Hd
    b1 = vec[i:i + Hd]; i += Hd
    W2 = vec[i:i + Hd]; i += Hd
    b2 = vec[i]; i += 1
    p = np.clip(vec[i:i + N_PARAMS], 0, 1)
    lerp = lambda r, x: r[0] + x * (r[1] - r[0])
    return {
        "W1": W1, "b1": b1, "W2": W2, "b2": b2,
        "thr": -1.0 + 3.0 * p[0],
        "stop": lerp(desk.stop_range, p[1]),
        "target": lerp(desk.target_range, p[2]),
        "hold": int(round(lerp(desk.hold_range, p[3]))),
    }


def score(dec, X):
    # runs in X's precision: training keeps its features in 32-bit, about 2-2.5x faster than 64-bit
    # with identical trades in testing (live trading still scores in full 64-bit)
    t = X.dtype
    return (np.tanh(X @ dec["W1"].astype(t, copy=False) + dec["b1"].astype(t, copy=False))
            @ dec["W2"].astype(t, copy=False) + t.type(dec["b2"]))


def trade_levels(dec, desk, atr):
    """Stop / target as fractions of entry price, from the brain's ATR multiples."""
    stop = max(dec["stop"] * atr, desk.min_stop)
    target = max(dec["target"] * atr, desk.min_stop)
    return stop, target


def simulate(vec, ds, desk, part="train", trades_out=None):
    n_feats = len(ds["feats"])
    dec = decode(vec, n_feats, desk)
    D = ds["date"]
    d0, d1 = (0, ds["split"]) if part == "train" else (ds["split"], ds["n_dates"])
    cache = ds.setdefault("_cache", {})            # same rows/features for every brain: slice them once
    if part not in cache:
        r0 = np.nonzero((D >= d0) & (D < d1))[0]
        cache[part] = (r0, np.ascontiguousarray(ds["X"][r0], dtype=np.float32))
    rows, Xp = cache[part]
    empty = {"fitness": -2.0, "trades": 0, "sharpe": 0.0, "total": 0.0, "winrate": 0.0,
             "avg": 0.0, "maxdd": 0.0}
    if len(rows) == 0:
        return empty
    s = score(dec, Xp)
    sel = s > dec["thr"]
    rows, s = rows[sel], s[sel]
    if len(rows) == 0:
        return empty

    intraday = desk.name == "day"
    maxpos = desk.max_positions
    per_day = maxpos if intraday else max(1, maxpos // 2)
    # Strictly chronological: within a day, intraday signals are taken in time
    # order (a live trader can't wait to see which bar of the day is best).
    # Ranking by score happens only among stocks at the SAME moment.
    tod = ds["tod"][rows]
    order = np.lexsort((-s, tod, D[rows]))
    rows = rows[order]
    d = D[rows]
    if not intraday:
        first = np.r_[True, d[1:] != d[:-1]]
        starts = np.maximum.accumulate(np.where(first, np.arange(len(d)), 0))
        keep = (np.arange(len(d)) - starts) < per_day * 3
        rows, d = rows[keep], d[keep]

    Hh = max(1, min(dec["hold"], desk.path_len))
    atr = ds["atr"][rows]
    stop = np.maximum(dec["stop"] * atr, desk.min_stop)
    tgt = np.maximum(dec["target"] * atr, desk.min_stop)
    lo, hi, cl = ds["lo"][rows, :Hh], ds["hi"][rows, :Hh], ds["cl"][rows, :Hh]
    sh = lo <= -stop[:, None]
    th = (cl if desk.close_targets else hi) >= tgt[:, None]
    fs = np.where(sh.any(1), sh.argmax(1), Hh)
    ft = np.where(th.any(1), th.argmax(1), Hh)
    r_ = np.arange(len(rows))
    stopped = (fs <= ft) & (fs < Hh)          # same bar = assume the stop hit first (conservative)
    targeted = ~stopped & (ft < Hh)
    fsi, fti = np.minimum(fs, Hh - 1), np.minimum(ft, Hh - 1)
    # gap through a level: fill at the middle of that bar, not at the level
    stop_fill = np.where(hi[r_, fsi] < -stop, (hi[r_, fsi] + lo[r_, fsi]) / 2, -stop)
    tgt_fill = np.where(lo[r_, fti] > tgt, (hi[r_, fti] + lo[r_, fti]) / 2, tgt)
    if desk.close_targets:
        tgt_fill = cl[r_, fti]                     # sold at that bar's close, like the live bot does
    ret = np.where(stopped, stop_fill, np.where(targeted, tgt_fill, cl[:, Hh - 1]))
    ret = ret - 2 * desk.cost_per_side
    exit_bar = np.where(stopped, fs, np.where(targeted, ft, Hh - 1))

    sym = ds["sym"][rows]
    step = desk.path_step
    pnl = np.zeros(d1 - d0)
    taken = []
    heap, next_free, day_used = [], {}, {}
    dl, sl, el, rl = d.tolist(), sym.tolist(), exit_bar.tolist(), ret.tolist()   # plain lists: faster loop
    n_rows, k = len(dl), 0
    while k < n_rows:
        dd, sy = dl[k], sl[k]
        if intraday:
            if day_used.get(dd, 0) >= maxpos or next_free.get(sy, -1) >= dd:
                k += 1
                continue
            day_used[dd] = day_used.get(dd, 0) + 1
            ed = dd
            next_free[sy] = dd
        else:
            while heap and heap[0] < dd:
                heapq.heappop(heap)
            if len(heap) >= maxpos:
                # portfolio full: skip straight to the first signal after the earliest exit
                k = max(k + 1, bisect_right(dl, heap[0]))
                continue
            if next_free.get(sy, -1) >= dd:
                k += 1
                continue
            ed = dd + 1 + el[k] * step + (step - 1)
            heapq.heappush(heap, ed)
            next_free[sy] = ed
        pnl[min(ed, d1 - 1) - d0] += rl[k] / maxpos
        taken.append(k)
        k += 1

    n = len(taken)
    if n == 0:
        return empty
    tr = ret[taken]
    eq = np.cumsum(pnl)
    maxdd = float(np.max(np.maximum.accumulate(np.r_[0, eq]) - np.r_[0, eq]))
    sd = pnl.std()
    sharpe = float(pnl.mean() / sd * math.sqrt(desk.periods_per_year)) if sd > 0 else 0.0
    need = desk.min_trades * (1.0 if part == "train" else 0.35)
    fitness = sharpe * min(1.0, n / need) - 2.0 * max(0.0, maxdd - 0.25)
    if n < 5:
        fitness = -2.0 + 0.01 * n
    if trades_out is not None:
        trades_out.extend({"date": ds["dates"][int(d[k])], "symbol": ds["symbols"][int(sym[k])],
                           "ret": float(ret[k]), "bars": int(exit_bar[k]) + 1} for k in taken)
    return {"fitness": round(float(fitness), 4), "trades": n, "sharpe": round(sharpe, 3),
            "total": round(float(pnl.sum()), 4), "winrate": round(float((tr > 0).mean()), 3),
            "avg": round(float(tr.mean()), 5), "maxdd": round(maxdd, 4)}


# ---------------------------------------------------------------- parallel scoring (uses several CPU cores)
_W = {}


def _worker_init(ds, desk):
    _W["ds"], _W["desk"] = ds, desk


def _worker_fit(vecs):
    return [simulate(v, _W["ds"], _W["desk"], "train")["fitness"] for v in vecs]


def make_pool(ds, desk, workers):
    """A pool of worker processes that already hold the dataset (keep it around to skip the ~2-3s start-up)."""
    import os
    from concurrent.futures import ProcessPoolExecutor
    # one math thread per worker process, otherwise they fight over the same cores
    for v in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
        os.environ[v] = "1"
    return ProcessPoolExecutor(max_workers=workers, initializer=_worker_init, initargs=(ds, desk))


def evolve(ds, desk, population=None, generations=12, pop_size=32, time_budget=90, seed=None, workers=1,
           pool=None, n_cands=6):
    """Runs the genetic algorithm. Returns (population, top candidates with
    train + holdout stats, generations run). workers > 1 scores brains on several CPU cores at once;
    pass a ready-made `pool` (from make_pool) to reuse one. n_cands = how many of the best brains
    (by training score) get checked on the unseen holdout data."""
    if pool is not None:
        return _evolve(ds, desk, population, generations, pop_size, time_budget, seed, pool, workers, n_cands)
    if workers > 1:
        try:
            pool = make_pool(ds, desk, workers)
        except Exception:
            pool = None
    try:
        return _evolve(ds, desk, population, generations, pop_size, time_budget, seed, pool, workers, n_cands)
    finally:
        if pool:
            pool.shutdown(wait=True, cancel_futures=True)


def _evolve(ds, desk, population, generations, pop_size, time_budget, seed, pool, workers, n_cands=6):
    rng = np.random.default_rng(seed)
    F = len(ds["feats"])
    G = n_genes(F)
    pop = [np.asarray(v, float) for v in (population or []) if len(v) == G][:pop_size]
    while len(pop) < pop_size:
        pop.append(random_vec(F, rng))
    t0 = time.time()
    gens = 0
    fits = None
    def score_all(vecs):
        if pool is None:
            return np.array([simulate(v, ds, desk, "train")["fitness"] for v in vecs])
        k = max(1, len(vecs) // workers + (len(vecs) % workers > 0))
        chunks = [vecs[i:i + k] for i in range(0, len(vecs), k)]
        return np.array([f for part in pool.map(_worker_fit, chunks) for f in part])

    for _ in range(generations):
        fits = score_all(pop)
        gens += 1
        # stop right after scoring, so the final population and its scores always belong together
        if gens >= generations or time.time() - t0 > time_budget:
            break
        order = np.argsort(-fits)
        new = [pop[i].copy() for i in order[:4]]                     # elites survive unchanged

        def pick():
            c = rng.choice(len(pop), 3, replace=False)
            return pop[c[np.argmax(fits[c])]]

        while len(new) < pop_size - 2:
            a, b = pick(), pick()
            child = np.where(rng.random(G) < 0.5, a, b)
            m = rng.random(G) < 0.15
            child[m] += rng.normal(0, 0.25, m.sum())
            child[-N_PARAMS:] = np.clip(child[-N_PARAMS:], 0, 1)
            new.append(child)
        new += [random_vec(F, rng) for _ in range(2)]                  # fresh blood
        pop = new
    if fits is None:
        fits = score_all(pop)
    order = np.argsort(-fits)
    pop = [pop[i] for i in order]                                     # best first (the lab keeps the top when reseeding)
    order = np.arange(len(pop))
    cands = []
    for i in order[:n_cands]:
        cands.append({"vec": pop[i], "train": simulate(pop[i], ds, desk, "train"),
                      "holdout": simulate(pop[i], ds, desk, "holdout")})
    cands.sort(key=lambda c: c["holdout"]["fitness"], reverse=True)
    return pop, cands, gens
