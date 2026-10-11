"""Turns raw price bars into the numbers the strategy brains look at.
The SAME functions feed both training (evolution) and live trading, so what
the bots learn on history is exactly what they see live."""
import numpy as np
import pandas as pd

from . import config, db
from .timeutil import ET, now_et

DAILY_FEATS = ["r1", "r5", "r20", "r60", "r120", "mom12_1", "d_sma20", "d_sma50", "d_sma200",
               "rsi", "atr", "vol20", "vratio", "bbpos", "d_hi252", "rs60", "spy_r20", "spy_d200"]
INTRA_FEATS = ["day_ret", "gap", "r1", "r4", "d_vwap", "rsi", "atr", "vratio", "tod", "orb",
               "d_hi_day", "prev_day_ret", "spy_day_ret", "spy_r4", "rs_day"]


def feature_names(desk_name):
    return INTRA_FEATS if desk_name == "day" else CRYPTO_FEATS if desk_name == "crypto" else DAILY_FEATS


def _rsi(c, n=14):
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rsi = 100 - 100 / (1 + up / dn.replace(0, np.nan))
    return (rsi.fillna(50) - 50) / 50


def _atr_pct(h, l, c, n=14):
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean() / c


# ---------------------------------------------------------------- daily
def _daily_one(g):
    c, h, l, v = g["c"], g["h"], g["l"], g["v"]
    f = pd.DataFrame(index=g.index)
    for n in (1, 5, 20, 60, 120):
        f[f"r{n}"] = c / c.shift(n) - 1
    f["mom12_1"] = c.shift(21) / c.shift(252) - 1
    for n in (20, 50, 200):
        f[f"d_sma{n}"] = c / c.rolling(n).mean() - 1
    f["rsi"] = _rsi(c)
    f["atr"] = _atr_pct(h, l, c)
    f["vol20"] = f["r1"].rolling(20).std()
    f["vratio"] = np.log((v + 1) / (v.rolling(20).mean() + 1))
    sd = c.rolling(20).std()
    f["bbpos"] = (c - c.rolling(20).mean()) / (2 * sd.replace(0, np.nan))
    f["d_hi252"] = c / h.rolling(252, min_periods=60).max() - 1
    f["dollar_vol"] = (c * v).rolling(20).mean()
    return f


def daily_features(bars: pd.DataFrame, bench: str = "SPY", hourly: bool = False) -> pd.DataFrame:
    """bars: symbol, ts, o, h, l, c, v (many symbols, must include the benchmark). Also used for hourly crypto
    bars, where the benchmark is BTC/USD and each "bar" is an hour instead of a day."""
    parts = []
    for sym, g in bars.groupby("symbol", sort=False):
        g = g.sort_values("ts")
        f = _daily_one(g)
        parts.append(pd.concat([g, f], axis=1))
    df = pd.concat(parts, ignore_index=True)
    spy = df[df.symbol == bench][["ts", "r20", "r60", "d_sma200"]].rename(
        columns={"r20": "spy_r20", "r60": "spy_r60", "d_sma200": "spy_d200"})
    df = df.merge(spy, on="ts", how="left")
    df["rs60"] = df["r60"] - df["spy_r60"]
    df["date"] = df["ts"] if hourly else df["ts"].dt.tz_convert(ET).dt.date
    return df


# ---------------------------------------------------------------- intraday
def _intra_one(g):
    g = g.sort_values("ts").reset_index(drop=True)
    et = g["ts"].dt.tz_convert(ET)
    date = et.dt.date
    mins = et.dt.hour * 60 + et.dt.minute
    c, h, l, o, v = g["c"], g["h"], g["l"], g["o"], g["v"]
    grp = g.groupby(date)
    f = pd.DataFrame(index=g.index)
    day_open = grp["o"].transform("first")
    day_close = grp["c"].last()
    prev_close = date.map(day_close.shift(1))
    prev_prev = date.map(day_close.shift(2))
    f["day_ret"] = c / day_open - 1
    f["gap"] = day_open / prev_close - 1
    f["r1"] = c / c.shift(1) - 1
    f["r4"] = c / c.shift(4) - 1
    tp = (h + l + c) / 3
    vwap = (tp * v).groupby(date).cumsum() / v.groupby(date).cumsum().replace(0, np.nan)
    f["d_vwap"] = c / vwap - 1
    f["rsi"] = _rsi(c)
    f["atr"] = _atr_pct(h, l, c)
    f["vratio"] = np.log((v + 1) / (v.rolling(130, min_periods=26).mean() + 1))
    f["tod"] = (mins - 570) / 390
    rank = grp.cumcount()
    or_high = h.where(rank < 2).groupby(date).cummax().groupby(date).ffill()
    f["orb"] = c / or_high - 1
    f["d_hi_day"] = c / h.groupby(date).cummax() - 1
    f["prev_day_ret"] = prev_close / prev_prev - 1
    out = pd.concat([g, f], axis=1)
    out["date"] = date
    out["mins"] = mins
    return out


def intraday_features(bars: pd.DataFrame) -> pd.DataFrame:
    parts = [_intra_one(g) for _, g in bars.groupby("symbol", sort=False)]
    df = pd.concat(parts, ignore_index=True)
    spy = df[df.symbol == "SPY"][["ts", "day_ret", "r4"]].rename(
        columns={"day_ret": "spy_day_ret", "r4": "spy_r4"})
    df = df.merge(spy, on="ts", how="left")
    df[["spy_day_ret", "spy_r4"]] = df[["spy_day_ret", "spy_r4"]].fillna(0.0)
    df["rs_day"] = df["day_ret"] - df["spy_day_ret"]
    return df


# ---------------------------------------------------------------- crypto (hourly, 24/7)
# Everything here is known at the moment the hourly bar closes - nothing peeks at the future:
#   price action over 1h..30d, trend, volatility regime, volume, how the coin moves vs Bitcoin,
#   how the whole crypto market is moving, time of day / week, Fear & Greed (lagged a full day),
#   and the US stock market's last completed session.
CRYPTO_FEATS = ["r1", "r4", "r12", "r24", "r72", "r168", "d_ema24", "d_ema72", "d_ema168", "d_ema720",
                "rsi", "atr", "vol24", "vol_ratio", "bbpos", "d_hi168", "d_lo168", "d_hi720", "vratio",
                "btc_r4", "btc_r24", "btc_r168", "btc_d_ema168", "rs24", "rs168", "breadth24",
                "hod_sin", "hod_cos", "dow_sin", "dow_cos", "fng", "fng_chg7", "spy_r1d", "spy_r5d"]
GAP_HOURS = 6   # a hole longer than this in a coin's history starts a new, separate stretch


def _crypto_one(g):
    c, h, l, v = g["c"], g["h"], g["l"], g["v"]
    f = pd.DataFrame(index=g.index)
    for n in (1, 4, 12, 24, 72, 168):
        f[f"r{n}"] = c / c.shift(n) - 1
    for n in (24, 72, 168, 720):
        f[f"d_ema{n}"] = c / c.ewm(span=n, adjust=False, min_periods=min(n, 168)).mean() - 1
    f["rsi"] = _rsi(c)
    f["atr"] = _atr_pct(h, l, c)
    f["vol24"] = f["r1"].rolling(24).std()
    f["vol_ratio"] = np.log(f["vol24"] / f["r1"].rolling(168).std())
    sd = c.rolling(24).std()
    f["bbpos"] = (c - c.rolling(24).mean()) / (2 * sd.replace(0, np.nan))
    f["d_hi168"] = c / h.rolling(168).max() - 1
    f["d_lo168"] = c / l.rolling(168).min() - 1
    f["d_hi720"] = c / h.rolling(720, min_periods=168).max() - 1
    # ratio to its own recent average (exchanges' reported volume levels changed over the years)
    f["vratio"] = np.log((v / v.rolling(24).mean().replace(0, np.nan)).clip(0.05, 20))
    return f


def crypto_features(bars: pd.DataFrame, bench: str = "BTC/USD") -> pd.DataFrame:
    """bars: hourly symbol, ts, o, h, l, c, v for several coins (must include the benchmark)."""
    from . import macro
    parts = []
    for sym, g in bars.groupby("symbol", sort=False):
        g = g.sort_values("ts").reset_index(drop=True)
        seg = (g["ts"].diff() > pd.Timedelta(hours=GAP_HOURS)).cumsum()
        for k, gg in g.groupby(seg, sort=False):
            gg = gg.reset_index(drop=True)
            out = pd.concat([gg, _crypto_one(gg)], axis=1)
            out["seg"] = f"{sym}#{k}"
            parts.append(out)
    df = pd.concat(parts, ignore_index=True)
    btc = df[df.symbol == bench][["ts", "r4", "r24", "r168", "d_ema168"]].drop_duplicates("ts").rename(
        columns={"r4": "btc_r4", "r24": "btc_r24", "r168": "btc_r168", "d_ema168": "btc_d_ema168"})
    df = df.merge(btc, on="ts", how="left")
    df["rs24"] = df["r24"] - df["btc_r24"]
    df["rs168"] = df["r168"] - df["btc_r168"]
    df["breadth24"] = df.groupby("ts")["r24"].transform("mean")
    t = df["ts"] + pd.Timedelta(hours=1)                                  # the moment the bar closes
    hod, dow = t.dt.hour + t.dt.minute / 60, t.dt.dayofweek + t.dt.hour / 24
    df["hod_sin"], df["hod_cos"] = np.sin(2 * np.pi * hod / 24), np.cos(2 * np.pi * hod / 24)
    df["dow_sin"], df["dow_cos"] = np.sin(2 * np.pi * dow / 7), np.cos(2 * np.pi * dow / 7)
    df["_t"] = t.astype("datetime64[ns, UTC]")
    df = df.sort_values("_t")
    # Fear & Greed: a day's reading counts only from the END of that day (+1 day), to be safe about when it's published
    fng = macro.fng_series()
    if not fng.empty:
        fng = fng.sort_values("ts")
        fng["fng"] = fng["value"] / 100 - 0.5
        fng["fng_chg7"] = fng["fng"] - fng["fng"].shift(7)
        fng["_t"] = (fng["ts"] + pd.Timedelta(days=1)).astype("datetime64[ns, UTC]")
        df = pd.merge_asof(df, fng[["_t", "fng", "fng_chg7"]], on="_t", direction="backward")
    else:
        df["fng"], df["fng_chg7"] = np.nan, np.nan
    # US stocks: SPY's last fully completed session (a session counts from the following midnight New York time)
    spy = db.load_bars(["SPY"], "1Day", (df["ts"].min() - pd.Timedelta(days=15)).isoformat())
    if not spy.empty:
        spy = spy.sort_values("ts")
        spy["spy_r1d"] = spy["c"] / spy["c"].shift(1) - 1
        spy["spy_r5d"] = spy["c"] / spy["c"].shift(5) - 1
        day = spy["ts"].dt.tz_convert(ET).dt.normalize()
        spy["_t"] = (day + pd.Timedelta(days=1)).dt.tz_convert("UTC").astype("datetime64[ns, UTC]")
        df = pd.merge_asof(df, spy[["_t", "spy_r1d", "spy_r5d"]].dropna(), on="_t", direction="backward")
    else:
        df["spy_r1d"], df["spy_r5d"] = np.nan, np.nan
    df["date"] = df["ts"]
    return df.drop(columns=["_t"]).sort_values(["symbol", "ts"]).reset_index(drop=True)


# ---------------------------------------------------------------- training dataset
def _paths(sub: pd.DataFrame, L: int, step: int, same_day: bool):
    """Future price path after each bar, relative to the NEXT bar's open (the
    realistic entry). Returns entry-valid mask and hi/lo/cl arrays (n, L)."""
    o, h, l, c = (sub[k].to_numpy(np.float64) for k in ("o", "h", "l", "c"))
    n = len(sub)
    idx = np.arange(n)
    if same_day:
        d = sub["date"].to_numpy()
        last = pd.Series(idx).groupby(d).transform("max").to_numpy()
    else:
        last = np.full(n, n - 1)
    valid = idx + 1 <= last
    entry = o[np.minimum(idx + 1, n - 1)]
    if step > 1:
        hmax = pd.Series(h[::-1]).rolling(step, min_periods=1).max().to_numpy()[::-1]
        lmin = pd.Series(l[::-1]).rolling(step, min_periods=1).min().to_numpy()[::-1]
    else:
        hmax, lmin = h, l
    hi = np.empty((n, L), np.float32)
    lo = np.empty((n, L), np.float32)
    cl = np.empty((n, L), np.float32)
    for j in range(L):
        s = idx + 1 + j * step
        e = np.minimum(s + step - 1, last)
        pad = s > last                     # past end of day / end of data: mark at last close
        s_ = np.minimum(s, last)
        hi[:, j] = np.where(pad, c[last], hmax[s_]) / entry - 1
        lo[:, j] = np.where(pad, c[last], lmin[s_]) / entry - 1
        cl[:, j] = np.where(pad, c[last], c[e]) / entry - 1
    return valid & (entry > 0), hi, lo, cl


def build_dataset(desk, bars: pd.DataFrame, holdout_frac=0.25, norm=None, start_dates=None, test_frac=0.0) -> dict:
    """Everything the simulator needs to replay history for one desk.
    start_dates {symbol: "YYYY-MM-DD"}: ignore a stock's history before that day
    (used for S&P 500 join dates, so the lab can't "invest" in a company before
    it was famous enough to be added to the index)."""
    intraday = desk.name == "day"
    feats = feature_names(desk.name)
    if intraday:
        df = intraday_features(bars)
        df = df[(df["mins"] >= 570) & (df["mins"] <= 915)]   # decide 09:30..15:15 bar closes
    elif desk.timeframe == "1Hour":
        now = pd.Timestamp.now(tz="UTC")
        bars = bars[bars["ts"] + pd.Timedelta(hours=1) <= now]           # completed hours only
        df = crypto_features(bars, desk.bench)
    else:
        today = now_et().date()
        bars = bars[bars["ts"].dt.tz_convert(ET).dt.date < today]   # completed days only
        df = daily_features(bars)
    df = df.replace([np.inf, -np.inf], np.nan)
    hourly = desk.timeframe == "1Hour"
    dates = np.array(sorted(df["date"].unique()))
    date_pos = {d: i for i, d in enumerate(dates)}
    X, ATR, HI, LO, CL, D, S, SYMS, T = [], [], [], [], [], [], [], [], []
    for _, sub in df.groupby("seg" if hourly else "symbol", sort=False):     # crypto: each unbroken stretch on its own
        sub = sub.reset_index(drop=True)
        valid, hi, lo, cl = _paths(sub, desk.path_len, desk.path_step, intraday)
        di = sub["date"].map(date_pos).to_numpy()
        m = valid & sub[feats + ["atr"]].notna().all(axis=1).to_numpy()
        m &= (sub["atr"] > 0).to_numpy() & ((sub["c"] >= 5).to_numpy() if not hourly else True)
        sd0 = (start_dates or {}).get(sub["symbol"].iloc[0])
        if sd0:
            m &= (sub["date"].astype(str) >= sd0).to_numpy()
        if not intraday and not hourly:
            m &= (sub["dollar_vol"] >= 5e6).fillna(False).to_numpy()
            m &= (di % desk.decision_step) == 0
        if not m.any():
            continue
        si = len(SYMS)
        X.append(sub.loc[m, feats].to_numpy(np.float32))
        ATR.append(sub.loc[m, "atr"].to_numpy(np.float32))
        HI.append(hi[m]); LO.append(lo[m]); CL.append(cl[m])
        D.append(di[m]); S.append(np.full(m.sum(), si, np.int32))
        T.append(sub.loc[m, "mins"].to_numpy(np.int32) if intraday else np.zeros(m.sum(), np.int32))
        SYMS.append(sub["symbol"].iloc[0])
    if not X:
        return None
    X = np.concatenate(X)
    D = np.concatenate(D).astype(np.int32)
    split = int(len(dates) * (1 - holdout_frac - test_frac))
    split2 = int(len(dates) * (1 - test_frac))
    train = D < split
    if norm is not None and len(norm[0]) == X.shape[1]:
        mu, sd = np.asarray(norm[0], np.float32), np.asarray(norm[1], np.float32)
    else:
        mu = X[train].mean(axis=0)
        sd = X[train].std(axis=0) + 1e-9
    return {
        "desk": desk.name, "feats": feats, "mu": mu, "sd": sd,
        "X": np.clip((X - mu) / sd, -4, 4).astype(np.float32),
        "atr": np.concatenate(ATR), "hi": np.concatenate(HI), "lo": np.concatenate(LO),
        "cl": np.concatenate(CL), "date": D, "tod": np.concatenate(T), "sym": np.concatenate(S),
        "n_dates": len(dates),
        "split": split, "split2": split2, "dates": [str(d) for d in dates], "symbols": SYMS,
    }


# ---------------------------------------------------------------- live
def live_features(desk, symbols, today_only=True) -> pd.DataFrame:
    """Latest feature row per symbol from completed bars in the database."""
    syms = sorted(set(symbols) | {desk.bench})
    if desk.timeframe == "1Hour":
        since = (pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=90)).isoformat()   # enough for the 30-day averages
        bars = db.load_bars(sorted(set(syms) | set(config.CRYPTO_UNIVERSE)), "1Hour", since)   # all coins: market breadth
        if bars.empty:
            return pd.DataFrame()
        bars = bars[bars["ts"] + pd.Timedelta(hours=1) <= pd.Timestamp.now(tz="UTC")]
        df = crypto_features(bars, desk.bench)
    elif desk.name == "day":
        since = (pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=12)).isoformat()
        bars = db.load_bars(syms, "15Min", since)
        if bars.empty:
            return pd.DataFrame()
        now = pd.Timestamp.now(tz="UTC")
        bars = bars[bars["ts"] + pd.Timedelta(minutes=15) <= now]      # completed bars only
        df = intraday_features(bars)
        if today_only:
            df = df[df["date"] == now_et().date()]
    else:
        since = (pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=420)).isoformat()
        bars = db.load_bars(syms, "1Day", since)
        if bars.empty:
            return pd.DataFrame()
        bars = bars[bars["ts"].dt.tz_convert(ET).dt.date < now_et().date()]
        df = daily_features(bars)
    if df.empty:
        return df
    last = df.sort_values("ts").groupby("symbol").tail(1).set_index("symbol")
    return last[last.index.isin(symbols)]
