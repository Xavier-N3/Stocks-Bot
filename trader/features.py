"""Turns raw price bars into the numbers the strategy brains look at.
The SAME functions feed both training (evolution) and live trading, so what
the bots learn on history is exactly what they see live."""
import numpy as np
import pandas as pd

from . import db
from .timeutil import ET, now_et

DAILY_FEATS = ["r1", "r5", "r20", "r60", "r120", "mom12_1", "d_sma20", "d_sma50", "d_sma200",
               "rsi", "atr", "vol20", "vratio", "bbpos", "d_hi252", "rs60", "spy_r20", "spy_d200"]
INTRA_FEATS = ["day_ret", "gap", "r1", "r4", "d_vwap", "rsi", "atr", "vratio", "tod", "orb",
               "d_hi_day", "prev_day_ret", "spy_day_ret", "spy_r4", "rs_day"]


def feature_names(desk_name):
    return INTRA_FEATS if desk_name == "day" else DAILY_FEATS


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


def build_dataset(desk, bars: pd.DataFrame, holdout_frac=0.25, norm=None, start_dates=None) -> dict:
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
        df = daily_features(bars, desk.bench, hourly=True)
    else:
        today = now_et().date()
        bars = bars[bars["ts"].dt.tz_convert(ET).dt.date < today]   # completed days only
        df = daily_features(bars)
    df = df.replace([np.inf, -np.inf], np.nan)
    hourly = desk.timeframe == "1Hour"
    dates = np.array(sorted(df["date"].unique()))
    date_pos = {d: i for i, d in enumerate(dates)}
    X, ATR, HI, LO, CL, D, S, SYMS, T = [], [], [], [], [], [], [], [], []
    for _, sub in df.groupby("symbol", sort=False):
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
    split = int(len(dates) * (1 - holdout_frac))
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
        "split": split, "dates": [str(d) for d in dates], "symbols": SYMS,
    }


# ---------------------------------------------------------------- live
def live_features(desk, symbols, today_only=True) -> pd.DataFrame:
    """Latest feature row per symbol from completed bars in the database."""
    syms = sorted(set(symbols) | {desk.bench})
    if desk.timeframe == "1Hour":
        since = (pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=20)).isoformat()
        bars = db.load_bars(syms, "1Hour", since)
        if bars.empty:
            return pd.DataFrame()
        bars = bars[bars["ts"] + pd.Timedelta(hours=1) <= pd.Timestamp.now(tz="UTC")]
        df = daily_features(bars, desk.bench, hourly=True)
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
