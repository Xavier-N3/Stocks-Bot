# AI Trading Firm (paper trading)

Eleven AI workers run together on your PC, around the clock, trading your **Alpaca paper account** (fake money):

```
                 ┌──────────────────────── MANAGER ────────────────────────┐
                 │ grades every trade · writes lessons · report every 8h    │
                 │ halves size if live results lag backtests · moves capital│
                 └───────────────▲─────────────────▲─────────────────▲──────┘
                                 │ closed trades   │                 │
 DAY DESK (20%)          SWING DESK (35%)              LONG-TERM DESK (45%)
 Day researcher  ──briefs──▶ Day trader      Swing researcher ─▶ Swing trader     Long researcher ─▶ Long investor
 movers, 15-min bars,        flat by 3:50pm  S&P 500 daily data,  holds 2-20 days  S&P 500 + SEC financials, holds weeks-months
 news + web, every 5 min                     news, every 3h                         news, daily; weekly rebalance

   CHALLENGER (devil's advocate) - debates pros vs cons of EVERY trade before the order is sent
                       TRAINING LAB (neuroevolution) - evolves every desk's brain after trades + nightly
```

## The Studio - watch and talk to the bots

Open **http://localhost:8765** (or double-click **The Studio** on your desktop, or run `./studio.ps1`). It runs 24/7
inside the same background process as the bots.

* **Studio** - a cozy top-down office: trading floor, coding lab, manager's office, debate room, kitchen, living room
  with a fireplace, and a library. The bots walk around: they work at their desks when their real agent is busy, take
  coffee breaks, chat with each other, nap on the sofa late at night, and react to real events (a researcher walks
  briefs over to its trader, the Challenger and trader meet in the debate room, Darwin cheers new champion brains,
  Morgan visits a desk to grade a closed trade). Lighting follows your clock. **Click any bot to chat with it** - it
  answers from live data about what it is doing, holding and thinking (chat can't place trades). Drag to look
  around, scroll to zoom, click the big market screen for the dashboard.
* **Dashboard** - equity, today's change, portfolio vs the S&P 500 chart, each desk's capital/positions/P&L and its
  brain's backtest, open positions, closed trades with grades, Challenger debates (pros/cons/verdict), evolution
  history, lessons, AI usage and whether the AI filters are helping.
* **Reports** - every 8-hour report, plus a "Write one now" button.

| Bot | Name | Job |
|---|---|---|
| Day researcher / trader | Scout / Dash | movers + news every 5 min / day trades, flat by 3:50pm |
| Swing researcher / trader | Sage / Swift | S&P 500 setups / holds 2-20 days |
| Long-term researcher / investor | Atlas / Oakley | SEC financials / holds weeks-months |
| Challenger | Devi | argues for and against every trade; can reject or shrink it |
| Manager | Morgan | grades trades, lessons, reports, moves capital |
| Training lab | Darwin | neuroevolution of every desk's brain |

* **Researchers** keep a database of 6 years of daily prices for ~520 stocks and 4 months of 15-minute bars, pull
  news (Alpaca/Benzinga + Google News from across the web) and company financials (SEC filings), rank stocks with
  their desk's brain, and have an AI analyst judge each one. Results go to the trader as *briefs*, and to a readable
  page per stock in `library/` (start at `library/INDEX.md`).
* **Traders** score the briefed stocks with the desk's evolved brain, let an AI portfolio manager veto red flags,
  size the trade under hard risk rules, and send a **bracket order** (entry + stop-loss + take-profit held by Alpaca,
  so you stay protected even if the PC crashes).
* **Challenger** (Devi) gets every trade the AI portfolio manager approved and builds the strongest case for and
  against it, then rules *proceed*, *reduce* (60% size) or *reject*. It keeps a scorecard of what rejected stocks did
  afterwards; if its rejections keep missing winners, it's automatically demoted to advisory.
* **Manager** reviews every closed trade (success/failure, grade A-F, compared to the S&P 500), writes a lesson the
  traders read before every future decision, and writes a full report at 00:30, 08:30 and 16:30 New York time to
  `reports/` (newest always `reports/LATEST.md`).
* **Training lab**: each desk's brain is a small neural network plus its trade rules (entry threshold, stop,
  target, holding time). A population of 32 brains is replayed over market history; the best breed and mutate
  (neuroevolution). It runs after every closed trade and at least daily, continuing from where it left off. A new
  brain replaces the live one **only if it also wins on recent data it never trained on**.

## How it gets better after every trade

1. Manager grades the trade and writes a lesson -> traders' AI reads the latest lessons before every decision.
2. Lab is asked to evolve that desk again with the newest market data.
3. If a desk's last 10-20 live trades fall short of what its backtest promised, its size is halved until it recovers.
4. If stocks the AI vetoed (or the Challenger rejected) end up doing better than the ones bought, that AI loses its
   veto (advisory only) until it earns it back.
5. Each week, 5% of capital moves from the weakest desk to the strongest (within limits).
6. A stock that loses 3 times in a row for a desk is benched for 5 days.

## Daily use

| Do this | Command (in PowerShell, in this folder) |
|---|---|
| First-time setup / check everything | `./setup.ps1` |
| Start (runs in background) | `./start.ps1` |
| Open the Studio | `./studio.ps1` or the desktop shortcut |
| Is it running? What do we hold? | `./status.ps1` |
| Read the latest report | open `reports/LATEST.md` |
| Report right now | `./report_now.ps1` |
| Stop | `./stop.ps1` |
| Auto-start when Windows logs in | `./install_autostart.ps1` (already installed) |
| Emergency: block all new buys | create an empty file named `STOP_TRADING` in this folder |

The PC must be on (and not asleep) for it to run 24/7. It keeps Windows from idle-sleeping while it runs, but
closing the laptop lid can still sleep it. Set *Settings > System > Power > Lid close = Do nothing* if needed.

## Phone notifications (free)

The bot pushes updates to your phone through **ntfy** (free, open source, no account), and it works the same on this
PC or on a cloud server. Install the **ntfy** app (iPhone or Android), tap **+**, and subscribe to the topic
name in `.env` (`NOTIFY_NTFY_TOPIC`). You'll get:

| notification | when |
|---|---|
| Desk bought | a position is opened |
| Position closed | with its % and $ profit/loss |
| New champion brain | every time a desk's lab promotes a better brain, in plain English |
| Report | every 8 hours, only if trades closed: equity, today's change, trades and P&L |
| Warning | loss limits, size cuts, AI demotions, a crashed agent |

**Check your equity any time:** every notification has **Equity now** and **Positions** buttons; tap one and the bot
replies within seconds. You can also subscribe to `<your topic>-ask` in the app and send `equity` or `positions`.
Pick which notifications you get with `NOTIFY_EVENTS`. Test anytime: `.venv\Scripts\python.exe -m trader.notify`.
Since a stopped bot can't send anything, add a free **healthchecks.io** check URL as `HEALTHCHECK_URL`. It gets
pinged every 5 minutes and alerts you (by email, or through ntfy too) if the pings stop.

## Risk profile (`RISK_PROFILE` in `.env`)

`conservative`, `balanced` or `aggressive` (this account uses **aggressive**: about 2x the risk per trade,
bigger positions, buying stops for the day at -6%). Every position size is a % of *current* equity, so gains
compound automatically: as the account grows, positions grow with it. Under $25k equity the day desk is limited to
3 day trades per 5 days (US pattern-day-trader rule), so it is capped at 2 positions on this account.

## Running 24/7 in the cloud

See [deploy/CLOUD.md](deploy/CLOUD.md): Oracle Cloud Always Free server + GitHub + Tailscale or Cloudflare Tunnel.

## Free AI setup (add keys to `.env`)

All of these are free; the system tries them in order and skips any that are out of quota, so it never stops:

1. **Groq** - https://console.groq.com/keys (no card). Main brain: `gpt-oss-120b` for decisions/reviews,
   `gpt-oss-20b` for reading news. ~1,000 requests and 200k tokens per day *per model*.
2. **Google Gemini** - https://aistudio.google.com/apikey (no card). Backup + long reading.
3. **Mistral** - https://console.mistral.ai (free Experiment plan, phone verification). Very large free allowance.
4. **OpenRouter** (optional) - 50 free requests/day.
5. **Ollama on this PC** - unlimited, slower; the last resort. Already installed.

If every AI is unavailable, the traders keep trading on the numbers alone (the AI is a filter, not the engine).

## Honest expectations

* Most strategies that look great on history fail live. That is why brains are judged on unseen data, start on
  *probation* (half size) until they show an edge, and why you should stay on **paper for months**.
* The stock list is today's S&P 500, so history looks better than reality (companies that collapsed were removed).
* Paper fills are friendlier than real ones. Compare results against simply holding SPY (every report does).
* Real money requires `ALPACA_PAPER=false` **and** `I_UNDERSTAND_LIVE_TRADING_RISK=true` in `.env`.

## Optional features (parked)

The [`optional/`](optional/README.md) folder holds finished features that are **not active** and not loaded by
the bot, each with instructions to switch it on later. Right now: a **forex desk** (needs a free OANDA account),
parked until the account is big enough for forex to make a difference, and a **crypto desk** (24/7 BTC/ETH/SOL/XRP).

## Files

`run.py` starts everything · `trader/agents/` the 8 workers · `trader/evolution.py` neuroevolution ·
`trader/llm.py` free-AI router · `trader/risk.py` hard limits · `data/market.db` all data ·
`logs/system.log` live log · `.venv/` the bot's own Python · `_old_version_backup.zip` the previous bot.
