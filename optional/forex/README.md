# Forex desk (parked: optional, not active)

A complete fifth trading desk that was built, tested, and then **parked here on purpose**. Nothing in this
folder is loaded by the bot. The running firm behaves exactly as if forex was never added.

**Why it's parked:** forex price moves are small (often 0.3–0.8% a day), so unless there's enough real money
behind it, the profits are tiny even when the strategy works. Turn it on when the account is big enough
for it to matter.

## What you get when you install it

- **Two new agents** in the Studio: **Sterling** (forex researcher 💱) and **Pip** (forex trader 🌍), at a teal
  desk in the lab's bottom-left corner, plus a fifth "Forex" card on the dashboard.
- Trades **EUR/USD, GBP/USD, USD/JPY, AUD/USD, USD/CAD, USD/CHF**, both up (long) and down (short), on hourly bars.
- Runs on a separate **OANDA** account, because Alpaca has no forex. A free OANDA *practice* (demo) account is the
  default; real money needs two extra settings on purpose.
- **Hours:** forex is open Sunday 5pm to Friday 5pm New York time (24/5, not 24/7). The desk opens no new trades
  after Friday noon and closes everything by Friday 4pm, so it never holds through the weekend gap.
- **The same strict rules as the crypto desk:**
  - a brain must pass validation, then a final exam on the newest data (never used to pick it), then a
    7-day live paper "shadow test" (15+ trades, profitable, 50%+ wins) before a single real order
  - every trade also needs the AI manager to say buy, Devi (the Challenger) to say proceed with at least 70%
    confidence, and non-negative research sentiment
  - every order carries its stop-loss and take-profit at OANDA (protected even if the PC is off)
  - first 10 trades at half size; trading is withdrawn after 3 straight losses or a net loss after 5 trades
- **Money limits:** `FOREX_BUDGET` (default $5,000, capped at the OANDA account's value), at most 0.5% of it at
  risk per trade, at most 3 open positions, and all positions together at most 3× the budget.

## How to install it later

### Step 1: get a free OANDA key
1. Sign up at **oanda.com** and open a free **Practice** (demo) account. (For real money later: a live account.)
2. In OANDA's web platform: **My Account → Manage API Access → Generate** a token. Copy it.

### Step 2: put the forex desk back into the bot
**First install the crypto desk** (`optional/crypto`): forex reuses its strict testing system.

> **Note (Oct 14, 2026):** the bot has changed a lot since forex was parked, so `install.ps1` now stops with
> merge conflicts (safely: it changes nothing). Ask Claude to install it; it can fit `forex_desk.patch` into
> the current code by hand.

Easiest: ask Claude **"install the forex desk from optional/forex"**.

Or do it yourself. In PowerShell, from the `Stocks bot` folder:

```powershell
.\stop.ps1
.\optional\forex\install.ps1
```

The script puts the forex desk back using git (from the saved bookmark `forex-desk-v1`, or from
`forex_desk.patch` if that doesn't work). If the bot's code has changed a lot since this was parked and neither
works, it stops without changing anything and tells you. In that case ask Claude to port it, using
`forex_desk.patch` as the blueprint.

### Step 3: add the key and start
Open `.env` and add:

```
OANDA_API_TOKEN=paste-your-token-here
```

Optional settings (defaults shown):

```
OANDA_ENV=practice            # "live" ALSO needs I_UNDERSTAND_LIVE_TRADING_RISK=true
FOREX_BUDGET=5000             # $ of the OANDA account the desk may use
FOREX_MAX_LEVERAGE=3          # all open forex positions together <= 3x the budget
MAX_POSITIONS_FOREX=3
FOREX_PAIRS=EUR_USD,GBP_USD,USD_JPY,AUD_USD,USD_CAD,USD_CHF
```

Then `.\start.ps1`. The desk downloads about 7 years of hourly prices and the lab trains it nonstop ("boost
mode") until a brain passes the bar. Expect days of training and 7+ days of shadow testing before any real trade,
and possibly no trades at all if no brain proves itself. That's the rules working as intended.

## How it's built (for whoever installs it)

| file | what changes |
|---|---|
| `trader/oanda.py` (new) | OANDA REST client: price history, live prices, market orders with stop-loss/take-profit attached, trade syncing, weekend rules, practice-only lock |
| `trader/config.py` | the `forex` desk definition and the `OANDA_*` / `FOREX_*` settings; `STRICT_DESKS = ("crypto", "forex")` |
| `trader/features.py` | 29 forex inputs (returns 1h→1 week, trend, volatility, tick volume, a US-dollar strength index, which way each instrument bets on the dollar, session/day cycles, the S&P 500's last session); each trading week kept as its own stretch |
| `trader/crypto_shadow.py` | the strict path (validation, final exam, shadow test, clearance, revoke) generalized to any strict desk |
| `trader/agents/lab.py` | boost training for every strict desk still in training, one warm worker pool per desk |
| `trader/agents/researcher.py`, `trader.py` | Sterling's research (web news per pair, live prices) and Pip's trading, weekend flattening and forex risk rules |
| `trader/evolution.py`, `features.py` | "one position per market" slots, so a pair and its short twin are never held together (US accounts can't hedge) |
| Studio (`world.js`, `app.js`, `style.css`, `studio_server.py`, `personas.py`), `run.py`, `report.py`, `manager.py`, `challenger.py` | the desk, the bots, the dashboard card, chat rules, report line, follow-ups |

**Shorts:** every pair also exists as a flipped twin (`EUR_USD_SHORT` = 1 ÷ EUR/USD), so "short EUR/USD" is just
"buy the flipped twin". The simulator, brains and shadow test stay long-only, and only `oanda.py` turns it into a
real sell order.

**Tested before parking:** the full training path (features → dataset → evolution → simulator) on 2.8 years of
real hourly forex prices. There were no Friday-afternoon entries, both directions were used, the Studio and
dashboard rendered, and the bot ran without errors. **Not tested:** the live OANDA connection itself (no key existed
yet). Watch `logs/system.log` the first time it connects.
