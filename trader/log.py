import logging
import sys
from logging.handlers import RotatingFileHandler

from . import config

_configured = False


def get(name: str) -> logging.Logger:
    global _configured
    if not _configured:
        fmt = logging.Formatter("%(asctime)s %(levelname)-7s [%(name)s] %(message)s")
        root = logging.getLogger("trader")
        root.setLevel(logging.INFO)
        fh = RotatingFileHandler(config.LOG_DIR / "system.log", maxBytes=5_000_000,
                                 backupCount=5, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)
        if sys.stdout is not None:          # pythonw (background) has no console
            sh = logging.StreamHandler(sys.stdout)
            sh.setFormatter(fmt)
            root.addHandler(sh)
        _configured = True
    return logging.getLogger(f"trader.{name}")
