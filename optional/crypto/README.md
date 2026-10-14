# Crypto desk (parked: optional, not active)

A complete 24/7 crypto desk that was built, trained and then **parked here on purpose**. Nothing in this folder
is loaded by the bot. The running firm trades only the stock desks (day, swing, long-term).

**Why it's parked:** after thousands of training runs, no crypto brain passed both the validation check and the
final exam on the newest data. Alpaca's crypto fees (~0.15–0.25% per trade) are also a heavy handicap for
short-term crypto trading. It made no trades and held nothing when it was parked.

## What you get when you install it

- **Two agents** in the Studio: **Nova** (crypto researcher 🪙) and **Satoshi** (crypto trader ₿), at a red desk in the
  lab's right corner, a **₿ Crypto ON/OFF** switch in the top bar, and a Crypto card on the dashboard.
- Trades **BTC, ETH, SOL and XRP** on hourly bars, 24/7, through your existing Alpaca account (no new account
  needed). The smallest budget in the firm: 5% of equity, max 2 positions.
- **The brain:** 34 inputs (price action from 1 hour to 30 days, volatility, volume, strength vs Bitcoin, the whole
  crypto market's direction, time of day/week, the Fear & Greed index, the S&P 500's last session), hourly history back
  to 2021 (including the 2022 crash), a small 4-neuron network, and at least 200 training trades required.
- **The strictest rules in the firm:**
  - a brain must pass validation, then a final exam on the newest 14 months (never used to pick it), then a
    7-day live paper "shadow test" (15+ trades, profitable, 50%+ wins) before a single real order
  - every trade also needs the AI manager to say buy, Devi to say proceed with at least 70% confidence, and
    non-negative research sentiment; the AI analyst also sees live funding rates and Fear & Greed
  - each buy gets a protective stop order at Alpaca; Satoshi sells at the target or the time limit
  - first 10 trades at half size; clearance withdrawn after 3 straight losses or a net loss after 5 trades
- Optional **boost mode** (`TRAINING_RUSH=true`): crypto trains nonstop until a brain passes. It's off by default
  because nonstop searching mostly finds brains that are lucky on the validation data.

## How to install it later

Easiest: ask Claude **"install the crypto desk from optional/crypto"**.

Or yourself, in PowerShell from the `Stocks bot` folder:

```powershell
.\stop.ps1
.\optional\crypto\install.ps1
.\start.ps1
```

The script re-adds everything by reversing the git change that parked it (saved bookmark: `crypto-removed`),
falling back to `crypto_desk.patch`. If the bot's code has changed too much for either to apply cleanly, it changes
nothing and tells you; then ask Claude to port it using `crypto_desk.patch` as the blueprint.

No keys needed. The bot's database still holds the crypto price history and the last trained brains, so it picks up
where it left off. Optional settings for `.env` after installing:

```
CRYPTO_ENABLED=true            # the Studio switch overrides this while running
SLEEVE_CRYPTO=0.05             # share of equity
MAX_POSITIONS_CRYPTO=2
CRYPTO_MIN_TRADES=200          # trades a brain must make in training
CRYPTO_HIDDEN=4                # brain size
TRAINING_RUSH=false            # true = train crypto nonstop until a brain passes
```

## Depends on / used by

- The parked **forex desk** (`optional/forex`) reuses crypto's strict testing system, so **install crypto first**,
  then forex.
- Bookmarks in git: `crypto-desk-v1` = the last version of the bot with crypto running; `crypto-removed` = the
  change that parked it.
