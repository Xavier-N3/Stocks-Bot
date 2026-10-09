from datetime import datetime, timezone
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


def now_et() -> datetime:
    return datetime.now(ET)


def et_minutes(dt: datetime = None) -> int:
    """Minutes since midnight, New York time (09:30 = 570, 16:00 = 960)."""
    dt = (dt or datetime.now(timezone.utc)).astimezone(ET)
    return dt.hour * 60 + dt.minute


def hhmm(s: str) -> int:
    h, m = s.strip().split(":")
    return int(h) * 60 + int(m)
