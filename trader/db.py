"""SQLite store shared by every agent: price history, news, research briefs,
trades, lessons, evolved strategies and AI usage. One connection per thread."""
import json
import sqlite3
import threading
from datetime import datetime, timezone

import pandas as pd

from . import config

_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS bars (symbol TEXT, tf TEXT, ts TEXT, o REAL, h REAL, l REAL,
    c REAL, v REAL, PRIMARY KEY (symbol, tf, ts));
CREATE TABLE IF NOT EXISTS news (id TEXT PRIMARY KEY, symbol TEXT, ts TEXT, source TEXT,
    headline TEXT, summary TEXT, url TEXT);
CREATE INDEX IF NOT EXISTS news_sym_ts ON news (symbol, ts);
CREATE TABLE IF NOT EXISTS fundamentals (symbol TEXT PRIMARY KEY, updated TEXT, data TEXT);
CREATE TABLE IF NOT EXISTS briefs (id INTEGER PRIMARY KEY AUTOINCREMENT, desk TEXT, symbol TEXT,
    created TEXT, expires TEXT, quant_score REAL, sentiment REAL, confidence REAL,
    catalysts TEXT, risks TEXT, summary TEXT, model TEXT, data TEXT);
CREATE INDEX IF NOT EXISTS briefs_desk ON briefs (desk, expires);
CREATE TABLE IF NOT EXISTS positions (id INTEGER PRIMARY KEY AUTOINCREMENT, desk TEXT,
    symbol TEXT, status TEXT, qty REAL, entry_price REAL, entry_time TEXT, stop_price REAL,
    target_price REAL, stop_pct REAL, exit_deadline TEXT, entry_order_id TEXT, exit_order_id TEXT,
    leg_ids TEXT, genome_id TEXT, score REAL, llm_json TEXT, features TEXT, exit_price REAL,
    exit_time TEXT, exit_reason TEXT, pnl REAL, pnl_pct REAL, reviewed INTEGER DEFAULT 0,
    grade TEXT, review TEXT);
CREATE INDEX IF NOT EXISTS positions_status ON positions (status, desk);
CREATE TABLE IF NOT EXISTS decisions (id INTEGER PRIMARY KEY AUTOINCREMENT, desk TEXT,
    symbol TEXT, ts TEXT, action TEXT, score REAL, llm_verdict TEXT, size_mult REAL,
    reason TEXT, price REAL, later_price REAL, later_ret REAL);
CREATE TABLE IF NOT EXISTS lessons (id INTEGER PRIMARY KEY AUTOINCREMENT, desk TEXT, ts TEXT,
    position_id INTEGER, grade TEXT, text TEXT);
CREATE TABLE IF NOT EXISTS genomes (id TEXT PRIMARY KEY, desk TEXT, created TEXT, role TEXT,
    train_fit REAL, holdout_fit REAL, holdout_trades INTEGER, data TEXT);
CREATE TABLE IF NOT EXISTS evolution_log (id INTEGER PRIMARY KEY AUTOINCREMENT, desk TEXT,
    ts TEXT, generations INTEGER, best_train REAL, best_holdout REAL, champion_holdout REAL,
    promoted INTEGER, note TEXT);
CREATE INDEX IF NOT EXISTS evolution_ts ON evolution_log (ts);
CREATE TABLE IF NOT EXISTS llm_usage (day TEXT, provider TEXT, model TEXT, requests INTEGER,
    tokens INTEGER, errors INTEGER, PRIMARY KEY (day, provider, model));
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT, updated TEXT);
CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, agent TEXT,
    level TEXT, msg TEXT);
CREATE TABLE IF NOT EXISTS debates (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, desk TEXT, symbol TEXT,
    price REAL, pros TEXT, cons TEXT, verdict TEXT, confidence REAL, reasoning TEXT, mode TEXT, model TEXT,
    later_ret REAL);
CREATE TABLE IF NOT EXISTS shadow_trades (id INTEGER PRIMARY KEY AUTOINCREMENT, genome_id TEXT, symbol TEXT,
    entry_ts TEXT, entry_price REAL, stop_pct REAL, target_pct REAL, deadline TEXT, exit_ts TEXT, exit_price REAL,
    ret REAL, exit_reason TEXT, status TEXT);
CREATE TABLE IF NOT EXISTS chats (id INTEGER PRIMARY KEY AUTOINCREMENT, agent TEXT, ts TEXT, role TEXT, text TEXT);
CREATE TABLE IF NOT EXISTS equity (ts TEXT PRIMARY KEY, equity REAL, cash REAL, spy REAL);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def conn() -> sqlite3.Connection:
    c = getattr(_local, "conn", None)
    if c is None:
        c = sqlite3.connect(config.DB_PATH, timeout=60)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        c.executescript(SCHEMA)
        _local.conn = c
    return c


def execute(sql, params=()):
    c = conn()
    cur = c.execute(sql, params)
    c.commit()
    return cur


def executemany(sql, rows):
    c = conn()
    c.executemany(sql, rows)
    c.commit()


def query(sql, params=()) -> list:
    return [dict(r) for r in conn().execute(sql, params).fetchall()]


def one(sql, params=()):
    r = conn().execute(sql, params).fetchone()
    return dict(r) if r else None


# ---------------------------------------------------------------- key/value
def kv_get(key, default=None):
    r = one("SELECT v FROM kv WHERE k=?", (key,))
    return json.loads(r["v"]) if r else default


def kv_set(key, value):
    execute("INSERT OR REPLACE INTO kv (k, v, updated) VALUES (?,?,?)",
            (key, json.dumps(value, default=str), now_iso()))


def event(agent, msg, level="info"):
    execute("INSERT INTO events (ts, agent, level, msg) VALUES (?,?,?,?)",
            (now_iso(), agent, level, msg[:2000]))
    from . import notify          # the important ones also go to your phone (if NOTIFY_NTFY_TOPIC is set)
    notify.on_event(agent, msg, level)


# ---------------------------------------------------------------- bars
def save_bars(df: pd.DataFrame, tf: str):
    """df: columns symbol, ts (UTC Timestamp), o, h, l, c, v."""
    if df is None or df.empty:
        return
    rows = [(r.symbol, tf, r.ts.isoformat(), float(r.o), float(r.h), float(r.l), float(r.c), float(r.v))
            for r in df.itertuples(index=False)]
    executemany("INSERT OR REPLACE INTO bars VALUES (?,?,?,?,?,?,?,?)", rows)


def load_bars(symbols, tf: str, since_iso: str = None) -> pd.DataFrame:
    """All stored bars for these symbols, sorted, ts as UTC Timestamps."""
    if isinstance(symbols, str):
        symbols = [symbols]
    out = []
    for i in range(0, len(symbols), 400):
        chunk = symbols[i:i + 400]
        q = f"SELECT symbol, ts, o, h, l, c, v FROM bars WHERE tf=? AND symbol IN ({','.join('?' * len(chunk))})"
        params = [tf, *chunk]
        if since_iso:
            q += " AND ts >= ?"
            params.append(since_iso)
        out.append(pd.read_sql_query(q, conn(), params=params))
    df = pd.concat(out, ignore_index=True) if out else pd.DataFrame()
    if df.empty:
        return pd.DataFrame(columns=["symbol", "ts", "o", "h", "l", "c", "v"])
    df["ts"] = pd.to_datetime(df["ts"], utc=True, format="ISO8601")
    return df.sort_values(["symbol", "ts"]).reset_index(drop=True)


def last_bar_ts(tf: str) -> dict:
    return {r["symbol"]: r["m"] for r in query("SELECT symbol, MAX(ts) m FROM bars WHERE tf=? GROUP BY symbol", (tf,))}
