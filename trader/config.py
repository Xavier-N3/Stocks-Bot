"""All settings, loaded from .env. Every value has a safe default so the
system runs with only the Alpaca keys filled in."""
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _s(key, default=""):
    return (os.getenv(key) or default).strip()


def _b(key, default):
    v = os.getenv(key)
    return default if v is None or not v.strip() else v.strip().lower() in ("1", "true", "yes", "on")


def _f(key, default):
    try:
        return float(os.getenv(key, default))
    except ValueError:
        return float(default)


def _i(key, default):
    try:
        return int(float(os.getenv(key, default)))
    except ValueError:
        return int(default)


def _list(key, default=""):
    return [x.strip().upper() for x in _s(key, default).split(",") if x.strip()]


# ---------------------------------------------------------------- Alpaca
ALPACA_API_KEY = _s("ALPACA_API_KEY")
ALPACA_SECRET_KEY = _s("ALPACA_SECRET_KEY")
ALPACA_PAPER = _b("ALPACA_PAPER", True)
# Second lock for real money: BOTH ALPACA_PAPER=false AND this must be set.
ALLOW_LIVE = _b("I_UNDERSTAND_LIVE_TRADING_RISK", False)

# ---------------------------------------------------------------- folders
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "market.db"
LIBRARY_DIR = ROOT / "library"
REPORTS_DIR = ROOT / "reports"
LOG_DIR = ROOT / "logs"
STATE_DIR = ROOT / "state"
KILL_SWITCH = ROOT / "STOP_TRADING"   # create this file to block all new buys
for _d in (DATA_DIR, LIBRARY_DIR, REPORTS_DIR, LOG_DIR, STATE_DIR):
    _d.mkdir(exist_ok=True)

# ---------------------------------------------------------------- AI providers
# Every provider is optional. The router tries them in chain order and
# silently skips any without a key or that are out of free quota.
GROQ_API_KEY = _s("GROQ_API_KEY")
GEMINI_API_KEY = _s("GEMINI_API_KEY")
MISTRAL_API_KEY = _s("MISTRAL_API_KEY")
OPENROUTER_API_KEY = _s("OPENROUTER_API_KEY")
OLLAMA_HOST = _s("OLLAMA_HOST", "http://localhost:11434")
LLM_ENABLED = _b("LLM_ENABLED", True)

# "provider:model" lists. "smart" = trade decisions, reviews, reports.
# "fast" = bulk news reading by the researchers.
LLM_CHAIN_SMART = _s(
    "LLM_CHAIN_SMART",
    "groq:openai/gpt-oss-120b,gemini:gemini-flash-latest,mistral:mistral-medium-latest,"
    "groq:qwen/qwen3.8-27b,openrouter:openai/gpt-oss-120b:free,ollama:qwen3.5:4b,ollama:llama3.2:3b",
)
LLM_CHAIN_FAST = _s(
    "LLM_CHAIN_FAST",
    "groq:openai/gpt-oss-20b,gemini:gemini-flash-lite-latest,mistral:mistral-small-latest,"
    "groq:qwen/qwen3.8-27b,ollama:qwen3.5:4b,ollama:llama3.2:3b",
)
# How the trader AIs use the language model:
#   veto     = AI can reject or resize a trade the quant model wants (default)
#   advisory = AI opinion is logged and measured but never blocks a trade
#   off      = no AI in trade decisions (research/reports still use it)
LLM_TRADE_MODE = _s("LLM_TRADE_MODE", "veto").lower()

# ---------------------------------------------------------------- data
SEC_USER_AGENT = _s("SEC_USER_AGENT", "StocksBot personal-research contact@example.com")
EXCLUDE_SYMBOLS = set(_list("EXCLUDE_SYMBOLS"))
EXTRA_SYMBOLS = _list("EXTRA_SYMBOLS")
DAY_CORE = _list(
    "DAY_CORE_SYMBOLS",
    "SPY,QQQ,IWM,AAPL,MSFT,NVDA,AMZN,GOOGL,META,AMD,AVGO,NFLX,JPM,BAC,XOM,CVX,"
    "UNH,LLY,COST,WMT,HD,V,MA,CRM,ORCL,ADBE,INTC,MU,QCOM,PLTR,UBER,COIN,SHOP,"
    "SMCI,ARM,DIS,BA,CAT,GS,PYPL",
)
ETFS = ["SPY", "QQQ", "IWM", "DIA", "VTI", "SCHD", "VNQ", "XLK", "XLF", "XLE", "XLV",
        "XLY", "XLP", "XLI", "XLU", "XLB", "XLRE", "XLC", "SMH", "GLD", "TLT"]

# ---------------------------------------------------------------- account risk
# How hard to press. Every size is a % of CURRENT equity, so gains compound automatically.
#   risk      = multiplier on the max loss allowed per trade (if its stop is hit)
#   slot      = multiplier on the normal position size
#   conv_cap  = biggest conviction boost a strong signal can get
#   daily     = account loss today at which all buying stops
#   desk_loss = multiplier on each desk's own daily loss limit
RISK_PROFILES = {
    "conservative": dict(risk=0.6, slot=0.8, conv_cap=1.3, daily=0.02, probation=0.35, reserve=0.05, desk_loss=0.75),
    "balanced":     dict(risk=1.0, slot=1.0, conv_cap=1.5, daily=0.03, probation=0.50, reserve=0.02, desk_loss=1.0),
    "aggressive":   dict(risk=2.0, slot=1.6, conv_cap=2.0, daily=0.06, probation=0.75, reserve=0.01, desk_loss=1.5),
}
RISK_PROFILE = _s("RISK_PROFILE", "balanced").lower()
PROFILE = RISK_PROFILES.get(RISK_PROFILE, RISK_PROFILES["balanced"])
ACCOUNT_DAILY_LOSS_PCT = _f("ACCOUNT_DAILY_LOSS_PCT", PROFILE["daily"])  # all desks stop buying for the day
CASH_RESERVE_PCT = _f("CASH_RESERVE_PCT", PROFILE["reserve"])          # never spend the last bit of cash
PROBATION_TRADING = _b("PROBATION_TRADING", True)   # trade smaller while no edge is proven yet
PROBATION_SIZE = _f("PROBATION_SIZE", PROFILE["probation"])
# US pattern-day-trader rule: below $25k (previous close), only 3 day trades per 5 business days.
# Below the line the desks automatically use fewer, bigger positions; above it they unlock full size.
PDT_EQUITY = 25_000.0
PDT_BUFFER = _f("PDT_BUFFER", 0.02)          # need 2% above $25k before unlocking, so a dip doesn't trip the rule
SMALL_ACCOUNT_MAX_POSITIONS = {"day": _i("SMALL_MAX_POSITIONS_DAY", 2), "swing": _i("SMALL_MAX_POSITIONS_SWING", 5),
                               "long": _i("SMALL_MAX_POSITIONS_LONG", 6)}
AUTO_REALLOCATE = _b("AUTO_REALLOCATE", True)   # manager moves money daily toward the desks making money
REALLOCATE_STEP = _f("REALLOCATE_STEP", 0.05)   # most a losing desk gives up per day (share of total equity)
MAX_NEW_PER_DAY = {"long": _i("MAX_NEW_PER_DAY_LONG", 2)}   # buy gradually instead of filling every slot at once
REPORT_TIMES_ET = _s("REPORT_TIMES_ET", "00:30,08:30,16:30")


@dataclass
class Desk:
    name: str
    title: str
    timeframe: str          # bar size the strategy runs on
    sleeve: float           # starting share of account equity
    sleeve_min: float
    sleeve_max: float
    max_positions: int
    risk_per_trade: float   # max equity lost if a stop is hit
    daily_loss_pct: float   # desk stops buying for the day past this loss (of its sleeve)
    cost_per_side: float    # spread + slippage assumed in simulation
    hold_range: tuple       # in bars of the simulated path
    stop_range: tuple       # stop distance as a multiple of ATR
    target_range: tuple
    min_stop: float         # floors so a stop is never inside the noise
    path_len: int           # bars of future the simulator looks at
    path_step: int          # 1 = every bar; 5 = weekly steps (long term)
    decision_step: int      # make a decision every N bars in simulation
    history_days: int
    train_symbols: int      # cap on symbols used for evolution (speed / memory)
    min_trades: int         # fewer simulated trades than this = untrustworthy
    evolve_cooldown_min: int
    periods_per_year: int = 252   # bars per year, for annualizing results
    bench: str = "SPY"            # the "market" each stock is compared against
    close_targets: bool = False   # True: a profit target only counts if a bar CLOSES above it (no spike fills)
    holdout_frac: float = 0.25    # most recent share of history kept unseen during training (used to pick champions)
    test_frac: float = 0.0        # an even later share, never used for picking: the final exam for a new champion
    robust_blocks: int = 1        # >1: training score = consistency across this many eras, not one lucky stretch
    hidden: int = 8               # neurons in the brain's hidden layer (smaller = simpler rules, less memorizing)
    max_stop: float = 0.5         # widest stop-loss allowed, as a fraction of the entry price (training AND live)


DESKS = {
    "day": Desk(
        name="day", title="Day trading", timeframe="15Min",
        sleeve=_f("SLEEVE_DAY", 0.20), sleeve_min=0.15, sleeve_max=0.60,
        max_positions=_i("MAX_POSITIONS_DAY", 5), risk_per_trade=0.005, daily_loss_pct=0.04,
        cost_per_side=0.0005, hold_range=(2, 26), stop_range=(0.5, 4.0), target_range=(0.5, 8.0),
        min_stop=0.004, path_len=26, path_step=1, decision_step=1,
        history_days=_i("DAY_HISTORY_DAYS", 120), train_symbols=60, min_trades=60,
        evolve_cooldown_min=15,
    ),
    "swing": Desk(
        name="swing", title="Swing trading", timeframe="1Day",
        sleeve=_f("SLEEVE_SWING", 0.35), sleeve_min=0.10, sleeve_max=0.40,
        max_positions=_i("MAX_POSITIONS_SWING", 8), risk_per_trade=0.0075, daily_loss_pct=0.05,
        cost_per_side=0.0005, hold_range=(2, 20), stop_range=(1.0, 5.0), target_range=(1.0, 10.0),
        min_stop=0.015, path_len=20, path_step=1, decision_step=1,
        history_days=_i("SWING_HISTORY_DAYS", 1100), train_symbols=700, min_trades=80,
        evolve_cooldown_min=45,
    ),
    "long": Desk(
        name="long", title="Long-term investing", timeframe="1Day",
        sleeve=_f("SLEEVE_LONG", 0.45), sleeve_min=0.25, sleeve_max=0.60,
        max_positions=_i("MAX_POSITIONS_LONG", 12), risk_per_trade=_f("RISK_PER_TRADE_LONG", 0.0125), daily_loss_pct=0.08,
        cost_per_side=0.0005, hold_range=(4, 50), stop_range=(3.0, 15.0), target_range=(4.0, 40.0),
        min_stop=0.06, path_len=50, path_step=5, decision_step=5,
        history_days=_i("LONG_HISTORY_DAYS", 2200), train_symbols=700, min_trades=60,
        evolve_cooldown_min=180,
        max_stop=_f("MAX_STOP_LONG", 0.15),   # a real safety net: never let a position fall more than 15%
        robust_blocks=3,                      # must make money in every era of its history, not one lucky run
    ),
}
# At most this many open positions per sector on a desk, so one sector's bad day can't sink the whole desk
MAX_PER_SECTOR = {"swing": _i("MAX_PER_SECTOR_SWING", 3), "long": _i("MAX_PER_SECTOR_LONG", 3)}
# Training schedule: the lab retrains each desk at least this often (minutes), in this priority order.
# Day trading gets fresh 15-minute data all day; long-term and swing get one new daily bar a day.
LAB_PRIORITY = _list("LAB_PRIORITY", "day,long,swing")
TRAIN_EVERY_MIN = {"day": _i("TRAIN_EVERY_MIN_DAY", 30), "long": _i("TRAIN_EVERY_MIN_LONG", 60),
                   "swing": _i("TRAIN_EVERY_MIN_SWING", 180)}
# brains per generation and generations per training run (bigger = deeper search per run)
RUN_SIZE = {"day": (32, 20), "long": (48, 25), "swing": (32, 15)}
# a new brain must beat the champion's unseen-data score by this much: with frequent retraining, small wins
# are often just luck
PROMOTE_MARGIN = _f("PROMOTE_MARGIN", 0.10)
DAY_CLOSE_ALL_MIN = 950        # 3:50pm New York: every day-trading position is closed by then
LAB_WORKERS = _i("LAB_WORKERS", max(1, min(6, (os.cpu_count() or 2) - 4)))  # CPU cores used for training


def validate():
    if not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
        raise SystemExit("Missing ALPACA_API_KEY / ALPACA_SECRET_KEY in .env")
    if not ALPACA_PAPER and not ALLOW_LIVE:
        raise SystemExit(
            "ALPACA_PAPER=false but I_UNDERSTAND_LIVE_TRADING_RISK is not true. "
            "Refusing to trade real money. Stay on paper until results are proven."
        )
