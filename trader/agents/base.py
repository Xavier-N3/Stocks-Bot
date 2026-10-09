import threading
import time
import traceback

from .. import db, log


class Agent:
    """A worker that loops forever. A crash in one step is logged and retried
    with backoff; it never takes down the other agents."""
    name = "agent"
    title = "Agent"

    def __init__(self):
        self.log = log.get(self.name)
        self.errors = 0
        self.stop_event = threading.Event()

    def step(self):
        raise NotImplementedError

    def activity(self, text: str):
        """What this agent is doing right now (shown in the Studio)."""
        db.kv_set(f"activity:{self.name}", {"at": db.now_iso(), "text": text[:200]})

    def wait_seconds(self) -> float:
        return 60

    def run(self):
        self.log.info("%s online", self.title)
        db.event(self.name, f"{self.title} started")
        while not self.stop_event.is_set():
            t0 = time.time()
            try:
                self.step()
                self.errors = 0
                status = "ok"
            except Exception as e:
                self.errors += 1
                status = f"error: {e}"
                self.log.error("step failed: %s\n%s", e, traceback.format_exc())
                db.event(self.name, f"error: {e}", "error")
            db.kv_set(f"heartbeat:{self.name}", {"at": db.now_iso(), "status": status,
                                                 "step_seconds": round(time.time() - t0, 1)})
            wait = self.wait_seconds() if self.errors == 0 else min(900, 30 * 2 ** min(self.errors, 5))
            self.stop_event.wait(wait)
        self.log.info("%s stopped", self.title)
