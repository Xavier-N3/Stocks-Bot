"""Starts the whole AI trading firm and keeps every agent alive 24/7.

    python run.py              run all agents (Ctrl+C to stop)
    python run.py --report     write a report right now and exit
"""
import os
import signal
import sys
import threading
import time

from trader import config, db, log, notify

L = log.get("supervisor")
PID = config.STATE_DIR / "run.pid"
STOP_FLAG = config.STATE_DIR / "stop.flag"


def make_agents():
    from trader.agents.lab import Lab
    from trader.agents.manager import Manager
    from trader.agents.researcher import Researcher
    from trader.agents.trader import Trader
    from trader.agents.challenger import Challenger
    return [Researcher("day"), Trader("day"), Researcher("swing"), Trader("swing"),
            Researcher("long"), Trader("long"),
            Challenger(), Manager(), Lab()]


def _alive(pid):
    try:
        import ctypes
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)   # works on Windows
        if h:
            code = ctypes.c_ulong()
            ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
            ctypes.windll.kernel32.CloseHandle(h)
            return code.value == 259    # STILL_ACTIVE
        return False
    except Exception:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False


def main():
    config.validate()
    if "--report" in sys.argv:
        from trader import report
        from trader.broker import Broker
        print("Report written:", report.write(Broker(), db.kv_get("last_report_at")))
        return
    if PID.exists():
        try:
            old = int(PID.read_text().strip())
            if old != os.getpid() and _alive(old):
                print(f"Already running (PID {old}). Use stop.ps1 first.")
                return
        except ValueError:
            pass
    PID.write_text(str(os.getpid()))
    try:   # keep Windows from going to sleep on idle while the firm runs
        import ctypes
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)   # CONTINUOUS | SYSTEM_REQUIRED
    except Exception:
        pass
    STOP_FLAG.unlink(missing_ok=True)
    L.info("=" * 70)
    L.info("AI trading firm starting  -  %s", "PAPER trading" if config.ALPACA_PAPER else "LIVE MONEY")
    L.info("=" * 70)

    # A different Alpaca account than last time? Reset the per-account timers and the "since start" baseline
    # (lessons, brains, research and history are kept).
    try:
        from trader.broker import Broker
        acct_id = str(Broker().client.get_account().id)
        prev_id = db.kv_get("account_id")
        if prev_id != acct_id:
            for r in db.query("SELECT k FROM kv WHERE k LIKE 'last_decide:%' OR k IN ('start', 'pdt_mode')"):
                db.execute("DELETE FROM kv WHERE k=?", (r["k"],))
            if prev_id:
                db.execute("UPDATE positions SET status='migrated', exit_reason='left in previous account' "
                           "WHERE status IN ('pending','open','closing')")
                db.event("supervisor", "New Alpaca account detected - desk timers reset, learning kept")
            db.kv_set("account_id", acct_id)
    except Exception as e:
        L.warning("account check failed: %s", e)

    from trader import llm
    for tier in ("smart", "fast"):
        av = llm.available(tier)
        L.info("AI models available (%s): %s", tier, ", ".join(av) if av else "NONE - running numbers-only")

    agents = make_agents()
    threads = {}

    def launch(i):
        a = agents[i]
        t = threading.Thread(target=a.run, name=a.name, daemon=True)
        t.start()
        threads[i] = t

    for i in range(len(agents)):
        launch(i)
        time.sleep(2)

    from trader import studio_server
    try:
        studio_server.start_background()
    except OSError as e:
        L.error("Studio could not start on port %s: %s", studio_server.PORT, e)

    stopping = threading.Event()

    def stop(*_):
        stopping.set()

    threading.Thread(target=notify.listen, args=(stopping,), daemon=True, name="phone").start()  # "Equity now" answers
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    try:
        while not stopping.is_set():
            if STOP_FLAG.exists():
                break
            for i, t in list(threads.items()):
                if not t.is_alive():           # should never happen, but never stay down
                    L.error("%s died - restarting it", agents[i].name)
                    db.event("supervisor", f"{agents[i].name} died and was restarted", "error")
                    agents[i] = make_agents()[i]
                    launch(i)
            notify.heartbeat()                 # optional "I'm alive" ping (HEALTHCHECK_URL)
            stopping.wait(15)
    finally:
        L.info("Shutting down agents...")
        for a in agents:
            a.stop_event.set()
        for t in threads.values():
            t.join(timeout=20)
        PID.unlink(missing_ok=True)
        STOP_FLAG.unlink(missing_ok=True)
        L.info("Stopped.")


if __name__ == "__main__":
    main()
