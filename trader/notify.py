"""Phone notifications, free, through ntfy (https://ntfy.sh): install the "ntfy" app on your phone, subscribe to
your private topic name (NOTIFY_NTFY_TOPIC in .env), and the bot pushes short updates to it. No account or key.
Works the same on this PC or a cloud server - it only needs outgoing internet.

What gets sent (NOTIFY_EVENTS in .env picks which):
  trade     - a desk opened a position
  close     - a position closed, with its profit/loss
  champion  - a desk's training lab promoted a new champion brain (every time, in plain English)
  report    - the 8-hourly report, with the headline numbers (only when trades actually closed)
  warning   - loss limits, size cuts, AI demotions and other warnings

Ask for your equity any time: every notification has an "Equity now" button. Tapping it (or sending any message
to the "<topic>-ask" topic, e.g. "equity" or "positions") makes the bot reply within seconds.

Optional: HEALTHCHECK_URL (e.g. a free healthchecks.io check) is pinged every few minutes while the bot runs, so
that service can alert you if the bot or its computer stops."""
import json
import threading
import time
import urllib.request
from datetime import datetime, timezone

from . import config, log

L = log.get("notify")
TOPIC = config._s("NOTIFY_NTFY_TOPIC")
SERVER = config._s("NOTIFY_NTFY_SERVER", "https://ntfy.sh").rstrip("/")
EVENTS = {e.lower() for e in config._list("NOTIFY_EVENTS", "trade,close,champion,report,warning")}
CLICK_URL = config._s("NOTIFY_CLICK_URL")            # e.g. your Studio's Tailscale address, opened when tapped
MAX_PER_DAY = config._i("NOTIFY_MAX_PER_DAY", 80)    # ntfy's free tier allows ~250/day; stay well under
ASK_TOPIC = f"{TOPIC}-ask" if TOPIC else ""         # phone -> bot: questions arrive here
HEALTHCHECK_URL = config._s("HEALTHCHECK_URL")

PRIORITY = {"trade": 3, "close": 3, "champion": 3, "stage": 4, "report": 2, "warning": 4, "reply": 3}
TAGS = {"trade": "chart_with_upwards_trend", "close": "moneybag", "champion": "dna", "stage": "trophy",
        "report": "memo", "warning": "warning", "reply": "bar_chart"}

_lock = threading.Lock()
_sent_today = {"day": "", "n": 0}
_last_key = {}


def enabled() -> bool:
    return bool(TOPIC)


def send(kind: str, title: str, message: str, key: str = None, cooldown_s: float = 0) -> bool:
    """Queue one notification (never blocks or breaks the caller). key + cooldown_s: skip repeats of the same key."""
    if not TOPIC or (kind not in EVENTS and kind != "reply"):
        return False
    with _lock:
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if _sent_today["day"] != day:
            _sent_today.update(day=day, n=0)
        if _sent_today["n"] >= MAX_PER_DAY and PRIORITY.get(kind, 3) < 4:
            return False                          # daily cap reached: only important ones still go out
        if key and cooldown_s and time.time() - _last_key.get(key, 0) < cooldown_s:
            return False
        if key:
            _last_key[key] = time.time()
        _sent_today["n"] += 1
    body = {"topic": TOPIC, "title": title[:120], "message": message[:1500], "priority": PRIORITY.get(kind, 3),
            "tags": [TAGS.get(kind, "bell")]}
    if CLICK_URL:
        body["click"] = CLICK_URL
    # one tap on any notification asks the bot for your equity (it answers with a new notification)
    body["actions"] = [{"action": "http", "label": "Equity now", "url": f"{SERVER}/{ASK_TOPIC}", "method": "POST",
                        "body": "equity", "clear": False},
                       {"action": "http", "label": "Positions", "url": f"{SERVER}/{ASK_TOPIC}", "method": "POST",
                        "body": "positions", "clear": False}]
    threading.Thread(target=_post, args=(body,), daemon=True).start()
    return True


def _post(body):
    for attempt in range(3):
        try:
            req = urllib.request.Request(SERVER, data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=15).read()
            return
        except Exception as e:
            if attempt == 2:
                L.warning("phone notification failed: %s", e)
            time.sleep(3 * (attempt + 1))


def on_event(agent: str, msg: str, level: str = "info"):
    """Called for every Studio feed event: pick out the ones worth a phone buzz."""
    if not TOPIC:
        return
    try:
        if msg.startswith("BUY "):
            desk = agent.replace("_trader", "")
            send("trade", f"{desk.title()} desk bought", msg[4:])
        elif msg.startswith("CLOSED "):
            send("close", "Position closed", msg[7:])
        elif level in ("warning", "error"):
            send("warning", f"Warning from {agent.replace('_', ' ')}", msg, key=f"warn:{msg[:60]}", cooldown_s=3600)
        elif msg.startswith("capital shifted"):
            send("warning", "Capital moved between desks", msg)
    except Exception as e:          # notifications must never break the bot
        L.warning("notification skipped: %s", e)


def champion(desk, title, h, old_fitness=None, data=None):
    """A desk's lab promoted a new champion brain: say what it scored, in plain words."""
    msg = (f"Tested on data it never learned from: {h.get('total', 0) * 100:+.1f}% over {h.get('trades', 0)} trades, "
           f"{h.get('winrate', 0) * 100:.0f}% winners. Quality score {h.get('fitness', 0):.2f}"
           + (f" (the old champion's: {old_fitness:.2f})." if old_fitness is not None else "."))
    send("champion", f"New {title} champion brain", msg)


def equity_text() -> str:
    from . import brain, db
    from .broker import Broker
    from .timeutil import now_et
    b = Broker()
    a = b.account()
    start = db.kv_get("start") or {}
    day = a["equity"] / a["last_equity"] - 1 if a["last_equity"] else 0
    since = a["equity"] / start["equity"] - 1 if start.get("equity") else None
    alp = b.alpaca_positions()
    upl = sum(p["upl"] for p in alp.values())
    midnight = now_et().replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc).isoformat()
    today = db.one("SELECT COUNT(*) n, SUM(pnl) p FROM positions WHERE status='closed' AND exit_time >= ?", (midnight,))
    def word(d):
        st = brain.status(brain.champion(d))
        if st == "active":
            return "trading"
        if st == "untrained":
            return "training"
        return "trading small"
    desks = ", ".join(f"{d} {word(d)}" for d in config.DESKS)
    return (f"${a['equity']:,.2f}  ({day * 100:+.2f}% today"
            + (f", {since * 100:+.2f}% since the start" if since is not None else "") + ")\n"
            f"Cash ${a['cash']:,.2f} · {len(alp)} positions open, unrealized ${upl:+,.2f}\n"
            f"Closed today: {today['n'] or 0} trades, ${today['p'] or 0:+,.2f}\n"
            f"Desks: {desks}")


def positions_text() -> str:
    from .broker import Broker
    b = Broker()
    alp = b.alpaca_positions()
    rows = []
    for p in b.ledger(None, ("open", "pending", "closing")):
        x = alp.get(p["symbol"], {})
        rows.append(f"{p['symbol']} ({p['desk']}): {x.get('uplpc', 0) * 100:+.2f}%  ${x.get('upl', 0):+,.2f}")
    return "\n".join(rows) or "No open positions."


def _answer(text: str):
    t = (text or "").strip().lower()
    try:
        if "pos" in t or "hold" in t:
            send("reply", "Open positions", positions_text())
        elif "help" in t:
            send("reply", "What you can ask", "equity - account value, today's change and desks\n"
                                               "positions - every open position with its profit/loss")
        else:
            send("reply", "Equity", equity_text())
    except Exception as e:
        send("reply", "Couldn't get your equity right now", f"{e}"[:300])


def listen(stop_event):
    """Wait for questions from the phone on the "-ask" topic (an outgoing connection, so it works behind any
    router or on a cloud server) and answer each one with a notification."""
    if not TOPIC:
        return
    last_id = None
    while not stop_event.is_set():
        try:
            url = f"{SERVER}/{ASK_TOPIC}/json" + (f"?since={last_id}" if last_id else "")
            with urllib.request.urlopen(url, timeout=120) as r:      # ntfy sends a keep-alive every ~45s
                for line in r:
                    if stop_event.is_set():
                        return
                    m = json.loads(line)
                    if m.get("event") == "message":
                        last_id = m.get("id")
                        _answer(m.get("message", ""))
        except Exception as e:
            L.debug("ask-topic connection dropped (%s) - reconnecting", e)
            stop_event.wait(10)


_last_ping = [0.0]


def heartbeat():
    """Ping HEALTHCHECK_URL at most every 5 minutes (called from the supervisor loop)."""
    if not HEALTHCHECK_URL or time.time() - _last_ping[0] < 300:
        return
    _last_ping[0] = time.time()

    def go():
        try:
            urllib.request.urlopen(HEALTHCHECK_URL, timeout=10).read()
        except Exception as e:
            L.warning("health-check ping failed: %s", e)
    threading.Thread(target=go, daemon=True).start()


if __name__ == "__main__":      # test:  .venv\Scripts\python.exe -m trader.notify
    if not TOPIC:
        raise SystemExit("Set NOTIFY_NTFY_TOPIC in .env first")
    EVENTS.add("report")
    send("report", "The Studio is connected", "You'll get trades, every new champion brain, reports and warnings "
                                              "here. Tap \"Equity now\" on any notification to see your account.")
    time.sleep(5)
    print(f"Sent a test notification to topic '{TOPIC}'")
