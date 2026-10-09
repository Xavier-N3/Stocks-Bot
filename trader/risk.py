"""Hard risk rules every desk must pass. No AI or evolved brain can override
these: capital sleeves, no margin, loss limits, kill switch, losing-streak blocks."""
import math
from datetime import datetime, timedelta, timezone

from . import config, db
from .timeutil import now_et


def sleeves() -> dict:
    s = db.kv_get("sleeves")
    if not s:
        s = {k: d.sleeve for k, d in config.DESKS.items()}
        db.kv_set("sleeves", s)
    return s


def size_mult(desk) -> float:
    """Manager-set multiplier (drops to 0.5 when live results fall short of backtests)."""
    return float(db.kv_get(f"size_mult:{desk}", 1.0))


def desk_exposure(desk, prices: dict) -> float:
    total = 0.0
    for p in db.query("SELECT symbol, qty, entry_price FROM positions WHERE desk=? AND "
                      "status IN ('pending','open','closing')", (desk,)):
        total += p["qty"] * prices.get(p["symbol"], p["entry_price"] or 0)
    return total


def pending_buys_notional() -> float:
    r = db.one("SELECT SUM(qty * entry_price) s FROM positions WHERE status='pending'")
    return float(r["s"] or 0)


def realized_today(desk) -> float:
    start = now_et().replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc).isoformat()
    r = db.one("SELECT SUM(pnl) s FROM positions WHERE desk=? AND status='closed' AND exit_time >= ?", (desk, start))
    return float(r["s"] or 0)


def symbol_blocked(desk, symbol, streak=3, days=5) -> bool:
    """A symbol that lost on its last 3 trades for this desk is benched for 5 days."""
    rows = db.query("SELECT pnl, exit_time FROM positions WHERE desk=? AND symbol=? AND status='closed' "
                    "ORDER BY exit_time DESC LIMIT ?", (desk, symbol, streak))
    if len(rows) < streak or any((r["pnl"] or 0) > 0 for r in rows):
        return False
    last = datetime.fromisoformat(rows[0]["exit_time"])
    return datetime.now(timezone.utc) - last < timedelta(days=days)


def can_buy(desk_name, account: dict, alp_positions: dict) -> tuple:
    desk = config.DESKS[desk_name]
    if config.KILL_SWITCH.exists():
        return False, "kill switch (STOP_TRADING file) is on"
    if account.get("blocked"):
        return False, "Alpaca says the account is blocked from trading"
    eq, last = account["equity"], account["last_equity"] or account["equity"]
    if last and (eq - last) / last <= -config.ACCOUNT_DAILY_LOSS_PCT:
        return False, f"account down {100 * (eq - last) / last:.1f}% today - daily loss limit"
    mine = {r["symbol"] for r in db.query("SELECT symbol FROM positions WHERE desk=? AND status='open'",
                                          (desk_name,))}
    unreal = sum(p["upl"] for s, p in alp_positions.items() if s in mine)
    sleeve_cap = eq * sleeves()[desk_name]
    if realized_today(desk_name) + (unreal if desk_name == "day" else 0) <=             -desk.daily_loss_pct * config.PROFILE["desk_loss"] * sleeve_cap:
        return False, "desk daily loss limit reached"
    n_open = len(db.query("SELECT id FROM positions WHERE desk=? AND status IN ('pending','open','closing')",
                          (desk_name,)))
    if n_open >= desk.max_positions:
        return False, f"desk at max positions ({desk.max_positions})"
    # Under $25k, only 3 day trades are allowed per 5 business days. Count open day positions as
    # day trades already "spent" (each one will be closed today), so we can never trip the limit.
    if desk_name == "day" and eq < 25_000 and account.get("daytrade_count", 0) + n_open >= 3:
        return False, "pattern-day-trader rule: under $25k, only 3 day trades per 5 days (limit reached)"
    return True, ""


def position_qty(desk_name, account, alp_positions, price, stop_pct, conviction) -> int:
    """Whole shares to buy. Conviction scales the base slot size; the risk profile scales everything."""
    desk = config.DESKS[desk_name]
    eq = account["equity"]
    prices = {s: p["price"] for s, p in alp_positions.items()}
    sleeve_cap = eq * sleeves()[desk_name]
    prof = config.PROFILE
    slot = sleeve_cap / desk.max_positions * prof["slot"] * max(0.25, min(conviction, prof["conv_cap"])) * size_mult(desk_name)
    room = sleeve_cap - desk_exposure(desk_name, prices)
    cash = account["cash"] - pending_buys_notional() - config.CASH_RESERVE_PCT * eq   # never use margin
    risk_cap = desk.risk_per_trade * prof["risk"] * eq / max(stop_pct, 1e-4)
    notional = min(slot, room, cash, risk_cap)
    if notional <= 0 or price <= 0:
        return 0
    return int(math.floor(notional / price))
