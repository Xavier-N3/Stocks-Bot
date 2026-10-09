"""Loads each desk's current champion brain and applies it to live data."""
import json

import numpy as np
import pandas as pd

from . import config, db, evolution


def champion(desk_name):
    r = db.one("SELECT * FROM genomes WHERE desk=? AND role='champion' ORDER BY created DESC LIMIT 1", (desk_name,))
    if not r:
        return None
    d = json.loads(r["data"])
    d["id"] = r["id"]
    d["vec"] = np.array(d["vec"], float)
    d["mu"] = np.array(d["mu"], np.float32)
    d["sd"] = np.array(d["sd"], np.float32)
    d["holdout_fit"] = r["holdout_fit"]
    d["train_fit"] = r["train_fit"]
    d["created"] = r["created"]
    return d


def status(champ) -> str:
    if champ is None:
        return "untrained"
    h = champ.get("holdout", {})
    desk = config.DESKS[champ["id"].split("-")[0]]
    if desk.name == "crypto":
        # The strictest bar in the firm: crypto only trades with a brain that clearly won on unseen data
        # AND in training - otherwise it sits out entirely (no probation trading).
        t = champ.get("train", {})
        strong = (h.get("trades", 0) >= 30 and h.get("sharpe", 0) >= 1.5 and h.get("winrate", 0) >= 0.55
                  and h.get("total", 0) > 0 and h.get("maxdd", 1) <= 0.15 and t.get("sharpe", 0) >= 1.0)
        return "active" if strong else "probation"
    enough = h.get("trades", 0) >= max(30, desk.min_trades * 0.35)
    if (champ["holdout_fit"] or 0) >= 0.5 and h.get("total", 0) > 0 and enough:
        return "active"
    return "probation"


def score_live(desk_name, champ, fdf: pd.DataFrame) -> pd.DataFrame:
    """Adds score / threshold / stop_pct / target_pct / hold columns."""
    desk = config.DESKS[desk_name]
    feats = champ["feats"]
    ok = fdf[feats + ["atr"]].replace([np.inf, -np.inf], np.nan).notna().all(axis=1) & (fdf["atr"] > 0)
    f = fdf[ok].copy()
    if f.empty:
        return f
    X = np.clip((f[feats].to_numpy(np.float32) - champ["mu"]) / champ["sd"], -4, 4)
    dec = evolution.decode(champ["vec"], len(feats), desk)
    f["score"] = evolution.score(dec, X)
    f["thr"] = dec["thr"]
    f["stop_pct"] = np.maximum(dec["stop"] * f["atr"], desk.min_stop).clip(upper=0.5)
    f["target_pct"] = np.maximum(dec["target"] * f["atr"], desk.min_stop)
    f["hold"] = dec["hold"]
    return f.sort_values("score", ascending=False)
