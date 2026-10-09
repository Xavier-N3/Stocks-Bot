"""Quick text status: python -m trader.status"""
from datetime import datetime, timezone

from . import brain, config, db, risk
from .broker import Broker


def main():
    b = Broker()
    a = b.account()
    start = db.kv_get("start") or {}
    chg = f"{(a['equity'] / start['equity'] - 1) * 100:+.2f}% since start" if start.get("equity") else ""
    print(f"\nAccount ({'PAPER' if config.ALPACA_PAPER else 'LIVE'}): equity ${a['equity']:,.2f}  cash ${a['cash']:,.2f}  {chg}")
    print("\nAgents:")
    for name in ["day_researcher", "day_trader", "swing_researcher", "swing_trader", "long_researcher",
                 "long_trader", "challenger", "manager", "lab"]:
        hb = db.kv_get(f"heartbeat:{name}") or {}
        age = ""
        if hb.get("at"):
            mins = (datetime.now(timezone.utc) - datetime.fromisoformat(hb["at"])).total_seconds() / 60
            age = f"{mins:.0f} min ago"
        print(f"  {name:18s} {age:>12s}  {hb.get('status', 'never ran')[:70]}")
    print("\nDesks:")
    sl = risk.sleeves()
    for d, desk in config.DESKS.items():
        c = brain.champion(d)
        h = (c or {}).get("holdout", {})
        n = db.one("SELECT COUNT(*) n, SUM(pnl>0) w, SUM(pnl) p FROM positions WHERE desk=? AND status='closed' "
                   "AND genome_id != 'legacy'", (d,))
        print(f"  {desk.title:20s} brain={brain.status(c):10s} capital={sl[d] * 100:.0f}%  open={len(b.ledger(d))}  "
              f"closed={n['n']} wins={n['w'] or 0} P&L=${n['p'] or 0:,.2f}  "
              f"(backtest on unseen data: {h.get('trades', 0)} trades, {100 * h.get('total', 0):+.1f}%)")
    alp = b.alpaca_positions()
    print("\nPositions:")
    for p in b.ledger(None):
        x = alp.get(p["symbol"], {})
        print(f"  [{p['desk']:5s}] {p['symbol']:6s} {p['status']:8s} qty {p['qty']:<10.4g} entry {p['entry_price'] or 0:9.2f} "
              f"now {x.get('price', 0):9.2f}  {100 * x.get('uplpc', 0):+6.2f}%")
    print("\nLast events:")
    for e in db.query("SELECT ts, agent, level, msg FROM events ORDER BY id DESC LIMIT 8"):
        print(f"  {e['ts'][5:16]} {e['agent']:16s} {e['level']:7s} {e['msg'][:110]}")
    print("\nLatest report: reports/LATEST.md\n")


if __name__ == "__main__":
    main()
