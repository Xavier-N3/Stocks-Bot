"""Phone notifications, free, through ntfy (https://ntfy.sh): install the "ntfy" app on your phone, subscribe to
your private topic name (NOTIFY_NTFY_TOPIC in .env), and the bot pushes short updates to it. No account or key.
Works the same on this PC or a cloud server - it only needs outgoing internet.

What gets sent (NOTIFY_EVENTS in .env picks which):
  trade     - a desk opened a position
  close     - a position closed, with its profit/loss
  champion  - a desk's training lab promoted a new champion brain (at most one per desk every few hours)
  stage     - a crypto brain passed its backtests, passed its shadow test, or lost its clearance
  report    - the 8-hourly report is ready, with the headline numbers
  warning   - loss limits, size cuts, AI demotions and other warnings

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
EVENTS = {e.lower() for e in config._list("NOTIFY_EVENTS", "trade,close,champion,stage,report,warning")}
CLICK_URL = config._s("NOTIFY_CLICK_URL")            # e.g. your Studio's Tailscale address, opened when tapped
MAX_PER_DAY = config._i("NOTIFY_MAX_PER_DAY", 80)    # ntfy's free tier allows ~250/day; stay well under
CHAMPION_COOLDOWN_H = config._f("NOTIFY_CHAMPION_COOLDOWN_HOURS", 6)
HEALTHCHECK_URL = config._s("HEALTHCHECK_URL")

PRIORITY = {"trade": 3, "close": 3, "champion": 2, "stage": 4, "report": 2, "warning": 4}
TAGS = {"trade": "chart_with_upwards_trend", "close": "moneybag", "champion": "dna", "stage": "trophy",
        "report": "memo", "warning": "warning"}

_lock = threading.Lock()
_sent_today = {"day": "", "n": 0}
_last_key = {}


def enabled() -> bool:
    return bool(TOPIC)


def send(kind: str, title: str, message: str, key: str = None, cooldown_s: float = 0) -> bool:
    """Queue one notification (never blocks or breaks the caller). key + cooldown_s: skip repeats of the same key."""
    if not TOPIC or kind not in EVENTS:
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
        elif "] new champion " in msg:
            desk = msg[1:msg.index("]")]
            brief = msg.split(": ", 1)[-1].split(" (old champion")[0]
            send("champion", f"New {desk} champion brain", brief, key=f"champ:{desk}",
                 cooldown_s=CHAMPION_COOLDOWN_H * 3600)
        elif "passed the full backtest bar" in msg or "passed shadow testing" in msg or "clearance withdrawn" in msg:
            send("stage", "Crypto desk update", msg)
        elif level in ("warning", "error"):
            send("warning", f"Warning from {agent.replace('_', ' ')}", msg, key=f"warn:{msg[:60]}", cooldown_s=3600)
        elif msg.startswith("capital shifted"):
            send("warning", "Capital moved between desks", msg)
    except Exception as e:          # notifications must never break the bot
        L.warning("notification skipped: %s", e)


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
    send("report", "The Studio is connected", "Test notification from your AI trading firm. You'll get trades, "
                                              "new champion brains, reports and warnings here.")
    time.sleep(5)
    print(f"Sent a test notification to topic '{TOPIC}'")
