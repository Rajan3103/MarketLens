# MarketLens

Market Analytics System with an Algorithmic Trading Framework for Indian markets (NSE / BSE),
similar in spirit to AlgoTest and StockMock.

MarketLens downloads index, stock, ETF and options data into PostgreSQL, shows live market
dashboards, builds and backtests option strategies minute by minute, replays past days for
practice, and forward-tests strategies on the live market with virtual money. It also builds
indicator strategies (with TradingView Pine Script export and webhook alerts), backtests an ETF
momentum portfolio after costs and capital gains tax, and keeps every strategy, trade and journal
note in one Strategy Library. Later it will place real orders through the **Dhan** API, run a
Telegram bot and run on an AWS server.

> Everything in this app is historical analysis for learning.
> It is **not** investment advice or a live trading recommendation.

**Safety first:** trading is **paper (virtual money) by default**. Real orders will only ever be
possible with `TRADING_MODE=live` in `.env`. Every page works without API keys, using clearly
labelled **DEMO** data.

---

## What works today

The menu has **Home**, **Settings & Status** and 12 modules in 4 groups.
✅ = ready · 🛠️ = partly ready · 📋 = upcoming

| Group | # | Module | What it does | Status |
| --- | --- | --- | --- | --- |
| Start | – | Home, Settings & Status | Module overview; which keys are set, PAPER/LIVE mode, database test button | ✅ |
| **Market & data** | 1 | Live Market Overview | Nifty, Bank Nifty, Fin Nifty, Sensex, India VIX cards; Nifty 50 gainers/losers and breadth (auto-refresh in market hours); sector heatmap; chart with moving averages, RSI and volume | ✅ |
| | 2 | Option Chain & Strategy Builder | Chain with PCR, max pain, ATM straddle, expected move, OI charts. Builder with 12 templates, editable legs, payoff today / at expiry, net Greeks, probability of profit, margin (Dhan or approximate); save / backtest / paper trade | ✅ |
| | 3 | Historical Data Downloader | Option-chain snapshots, expired options (1-minute, ATM±10), stocks / ETFs / indices daily, Dhan intraday candles; "What is stored" | ✅ |
| **Options testing** | 4 | Options Backtester | Multi-leg strategies on stored 1-minute option data (see below); 5 ready templates; equity curve, drawdown, day / month heatmaps, trade list with exit reasons, Excel / CSV | ✅ |
| | 5 | Option Simulator | Replay a stored day minute by minute and trade it with virtual money | ✅ |
| | 6 | Forward Test / Paper Trading | Run a saved strategy every minute on live Dhan (or DEMO) prices through the paper broker, as a background process; history vs backtest | ✅ (paper only) |
| **Indicator & portfolio** | 7 | Signals & Indicator Strategy Builder | Indicator rules without code (AND / OR); backtest with entry / exit markers; scanner; Pine Script v6 export with webhook alerts; webhook receiver (FastAPI) that saves alerts, sends Telegram messages and places paper orders for "auto" strategies | ✅ |
| | 8 | Momentum Engine Backtest | Ranking engine ready (`marketlens/momentum/ranking.py`: scores, filters, Nifty 50 index filter); backtest and page next | 🛠️ |
| | 9 | ETF Momentum Portfolio | Top-K ETF rotation backtest after costs and capital gains tax, vs Nifty 50; check a claimed return; parameter sweep; current signal | ✅ |
| **Control centre** | 10 | Personal Analytics & Strategy Library | Strategy library (open anywhere, duplicate, rename, delete, JSON export / import), holdings & P&L, trade journal, combined performance vs Nifty 50 | ✅ |
| | 11 | Live Algo Trading & Bot Monitor | | 📋 |
| | 12 | Telegram Bot Control Panel | | 📋 |

### Highlights of the ready modules

**4. Options Backtester**
- Underlying (NIFTY, BANKNIFTY, FINNIFTY, SENSEX), date range, weekly/monthly and current/next
  expiry, entry and exit time, days of the week, skip expiry day.
- **Strategy type** radio: **Short straddle** (ATM call + put), **Short strangle** (CE ATM+1 … ATM+10
  and PE ATM-1 … ATM-10 from drop-downs) or **Premium based** (enter a CE and a PE premium; at the
  entry time the strikes nearest those premiums are sold), or **Custom legs** for anything else.
- Legs: buy/sell, CE/PE, lots; strike by **ATM ± N**, **closest premium** or **closest delta**.
- Per leg: **SL and target** in %, points or Rs (checked on each minute's high/low; if both are
  touched in one minute the SL is assumed and noted), **trailing SL**, **lock profit**,
  **re-entry** (at cost, or re-execute with a new strike) and "move other legs' SL to cost".
- Strategy level: **range breakout** entry (on the index or each leg's own price),
  **overall M2M** stop loss / target / lock / trailing.
- **No look-ahead:** a decision at 09:20 uses the close of the 09:19 candle.
- Fast: one year of NIFTY 1-minute data with a 4-leg strategy runs in about 25–30 seconds.
- Ready templates: 9:20 short straddle, 0.20-delta short strangle, Rs 50 premium strangle,
  iron condor, breakout option buying.
- Buttons: Save to Strategy Library, Open a day in the Simulator, Start forward test.

**5. Option Simulator**
- Play, Pause, +1 / +5 minutes, speed 1x–30x, jump to a time, time slider.
- Shows only what was known at that minute: index candles so far, option chain with LTP / IV /
  delta / OI, mini payoff chart of open positions.
- Virtual orders with optional SL / target that trigger as the replay moves; Exit all;
  costs from `costs.py`; live P&L curve; end-of-day summary; sessions saved and listed.

**6. Forward Test / Paper Trading**
- Runs a strategy saved from the backtester (same rule code, so live and backtest behave the
  same) or a simple indicator strategy, every minute in market hours.
- Runs as a background process, so it keeps going when the browser is closed:
  `python -m marketlens.trading.forward --strategy <id>`.
- Status (waiting for entry time / watching for breakout / in trade / done for the day),
  open legs, today's trades with exit reasons, day-by-day history next to the backtest.

**7. Signals & Indicator Strategy Builder**
- **Build & backtest:** condition rows like "EMA(9) crosses above EMA(21)" or "RSI(14) > 50", joined
  with AND / OR, on SMA, EMA, RSI, MACD, Bollinger, Supertrend, VWAP, ATR and prices. Any NSE symbol,
  daily or 1–60 minute candles, SL / target %, costs. A rule true at a candle close fills at the next
  open. Chart with the indicators and entry / exit markers, plus the full results report.
- **Scanner:** where the entry rule is true on the latest daily candle across Nifty 50, Nifty 200,
  ETFs or your own list (a historical signal, not a recommendation).
- **Pine Script export:** the same rules as TradingView Pine Script v6 (inputs for every setting,
  Indian costs, alert() calls with MarketLens webhook JSON and a secret placeholder). Every
  generated script is checked by a validator; download as .pine; 5-step TradingView guide.
- **Webhook alerts:** your webhook address (this computer now, the AWS address later), whether
  `WEBHOOK_SECRET` is set, the JSON for TradingView and Chartink, and the last 50 events.
- **Save to Strategy Library**, optionally with **Auto**: webhook alerts naming the strategy then
  place paper orders by themselves.
- **Webhook receiver** (`python -m marketlens.webhook.server`): checks the secret (401 if wrong),
  ignores the same alert repeated within 10 s, saves every event and a signal, sends a Telegram
  message, and places a paper order for "auto" strategies.

**9. ETF Momentum Portfolio**
- Tick the ETFs (all from `data/universe/etfs.csv`), top K (1–5), lookbacks (1 / 3 / 6 / 12 months)
  with weights, score (return, return / volatility, Sharpe-style), monthly or weekly rebalance,
  absolute momentum filter (an ETF must itself be rising, else its slot goes to LIQUIDBEES or cash),
  start date and capital.
- **No look-ahead:** ranked at the close of the last trading day of the month / week, traded at the
  next day's close (Yahoo's ETF open prices are unreliable, so they are not used).
- **Costs at every rebalance:** brokerage, STT (0.001% on equity-ETF sells; none on gold, silver,
  international or liquid ETFs), exchange, SEBI, stamp duty, GST, DP charge and slippage, with
  whole units only. **Capital gains tax** with today's Indian rules (equity 20% / 12.5% above
  Rs 1.25 lakh; gold / silver / international at your slab or 12.5% after 12 months; liquid at your
  slab), losses set off and carried forward; all rates are settings.
- **Check a claim:** type e.g. "670% from Nov-2016": the same settings run without costs, with costs
  and with tax, next to the claim and Nifty 50, with how many points costs and tax took.
- Results vs Nifty 50 (NIFTYBEES with dividends): metrics table, equity curve, drawdown, yearly
  returns, holdings, every rebalance, tax by financial year, sold lots, Excel report.
- **Parameter sweep** (K = 1..5 × six lookback sets) to see how fragile the result is;
  **current signal** from the last stored close; **Save to Strategy Library**.
- **Data repairs:** Yahoo stored a few days of NIFTYBEES, BANKBEES, PSUBNKBEES, GOLDBEES and MON100
  at 1/10 or 1/100 of the price around unit splits; these are repaired using only earlier prices.
  LIQUIDBEES' interest is paid as extra units that Yahoo's prices only partly show, so by default
  it earns a fixed yearly yield you set (5%).

**10. Personal Analytics & Strategy Library**
- **Strategy library:** every saved strategy (options, indicator, momentum, ETF) with its type, where it
  was made, created date, last backtest (P&L, CAGR, drawdown, Sharpe, win rate) and forward-test results.
  Buttons: Open in backtester (Options / Signals / ETF page, filled in), Open in simulator, Start forward
  test, Deploy live (module 11), Duplicate, Rename, Delete (with a confirm tick; refused while a forward
  test runs), Export / Import as JSON.
- **Holdings & P&L:** Dhan holdings and today's positions when the keys are set, paper positions
  otherwise: invested, current value, day P&L, open and total P&L, allocation by sector. One snapshot a
  day goes into `holdings_snapshots` for the portfolio value chart.
- **Trade journal:** every trade from paper orders, forward tests, live orders and the simulator, rebuilt
  as round trips (open -> close, average price, costs shared by quantity). Filters by source, strategy,
  dates, symbol, tag and result; notes, tags (setup / mistake / emotion, or your own), a 1-5 rating and a
  screenshot (saved in `data/journal/`). Stats by tag, weekday and strategy.
- **Performance:** combined realised P&L curve vs Nifty 50, drawdown, monthly and daily heatmaps, win rate,
  best / worst days, profit factor and costs (simulator trades optional, as they are replayed past days).

---

## Setup, step by step (Windows + VS Code, or Ubuntu)

You need **Python 3.11 or newer**, **Git** and **PostgreSQL**. Check Python and Git in a
terminal (VS Code: menu **Terminal > New Terminal**):

```bash
python --version
git --version
```

### 1. Get the code

```bash
git clone https://github.com/Rajan3103/MarketLens.git
cd MarketLens
```

### 2. Make a virtual environment (a private box for this project's packages)

```bash
python -m venv .venv
```

Turn it on. You must do this every time you open a new terminal:

```bash
# Windows (PowerShell)
.venv\Scripts\Activate.ps1
# Ubuntu / Mac
source .venv/bin/activate
```

You will see `(.venv)` at the start of the line when it is on.
If PowerShell says scripts are disabled, run this once and try again:
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

### 3. Install the packages

```bash
pip install -r requirements.txt
```

The same `requirements.txt` works on Windows and Ubuntu. Every package has a comment saying
what it is for.

### 4. Add your keys (optional)

Copy `.env.example` to a new file called `.env` and fill in what you have. Each line in
`.env.example` has a comment saying what it is for. The `.env` file stays on your computer
and is never uploaded. Restart the app after changing it.

| Setting | What it is for | Without it |
| --- | --- | --- |
| `DHAN_CLIENT_ID`, `DHAN_ACCESS_TOKEN` | Live option chain, expired options data, margin, (later) orders. Dhan is the only broker | Clearly labelled DEMO data |
| `DATABASE_URL`, `TEST_DATABASE_URL` | Your PostgreSQL database and a separate test database | Database pages show how to fix it |
| `TRADING_MODE` | `paper` (default) or `live` | Paper |
| `PAPER_CAPITAL` | Virtual money for the paper broker (default 500000) | Rs 5,00,000 |
| `MAX_DAILY_LOSS_RS`, `MAX_OPEN_POSITIONS`, `MAX_ORDERS_PER_SECOND` | Risk limits | Safe defaults |
| `WEBHOOK_SECRET` | Checks TradingView / Chartink alerts sent to the webhook receiver (module 7) | The receiver refuses every alert |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | A Telegram message for every webhook alert (module 7); the bot later (module 12) | No messages are sent |

**Settings & Status** shows which keys are still missing.

### 5. Set up the database (PostgreSQL)

MarketLens keeps its data in **PostgreSQL** (17 or newer; 18 works). Install it from
https://www.postgresql.org/download/ (Windows) or with `sudo apt install postgresql`
(Ubuntu), and remember the password you choose for the `postgres` user.

Put that password in `.env` (replace `YOUR_PASSWORD`):

```
DATABASE_URL=postgresql+psycopg://postgres:YOUR_PASSWORD@localhost:5432/marketlens
TEST_DATABASE_URL=postgresql+psycopg://postgres:YOUR_PASSWORD@localhost:5432/marketlens_test
```

Then create the database and all its tables with one command:

```bash
python scripts/init_db.py
```

It creates the database if it is missing, runs the Alembic migrations and prints every table
with its row count. It is safe to run again. **Settings & Status** also has a
*Test database connection* button.

**After every `git pull`, bring the tables up to date:**

```bash
alembic upgrade head
```

Migrations so far:

| Migration | What it adds |
| --- | --- |
| 0001 | The first 24 tables (instruments, prices, options, strategies, backtests, orders, positions, ...) |
| 0002 | `price_bars` for index / stock / futures candles; futures contracts |
| 0003 | `download_progress` (resume long downloads); DEMO flags |
| 0004 | Backtest trades keep their symbol and costs |
| 0005 | Backtest trades keep a note (e.g. "SL and target both touched: assumed SL") |
| 0006 | Simulator orders keep symbol, costs and reason |
| 0007 | Orders and positions keep symbol, Security ID, segment, tag, costs (paper broker) |
| 0008 | One forward-test row per strategy per day, with state, message and heartbeat |
| 0009 | Trade journal: link to the trade, source, 1-5 rating; daily whole-account holdings snapshots |

When `marketlens/models.py` changes, make and apply a new migration:

```bash
alembic revision --autogenerate -m "what changed"
alembic upgrade head
```

### 6. Download prices into the database

```bash
python scripts/sync_prices.py --universe etfs --years 10
python scripts/sync_prices.py --universe nifty200 --years 10
```

The momentum ranking (module 8) also needs the **Nifty 50 index** for its "only buy when Nifty 50 is
above its 200-day average" filter. Download it once (later runs of the line add the missing days):

```bash
python -c "import datetime as d; from marketlens.ingest.prices import sync_prices; print(sync_prices(['^NSEI'], d.date(2016, 1, 1)))"
```

The first run downloads everything (about 1.5 minutes for Nifty 200 × 10 years); later runs
only fetch the missing days. Run it after market close each day. The stock lists live in
`data/universe/`; NSE changes the Nifty lists twice a year, so refresh them with:

```bash
python scripts/update_universe.py
```

### 7. Download option data (for the backtester and simulator)

```bash
python scripts/download_options.py index    --underlying NIFTY --from 2026-09-01 --to 2026-10-23
python scripts/download_options.py expired  --underlying NIFTY --from 2026-09-01 --to 2026-10-23
python scripts/download_options.py snapshot --underlying NIFTY
```

`expired` stores minute bars for ATM-10 .. ATM+10, calls and puts (change with `--width`,
`--types CE`, `--flag monthly`). Long downloads can be stopped with `Ctrl + C` and run again:
finished days are skipped. Without Dhan keys the same commands store clearly marked DEMO data;
the first real Dhan download deletes it. The same downloads are on page **3** with progress bars.

### 8. Run the app

```bash
streamlit run app.py
```

Your browser opens at http://localhost:8501. Use the menu on the left to switch pages.
Press `Ctrl + C` in the terminal to stop the app.

### 9. Run a forward test in the background (module 6)

Start it from page **6** (Start / Stop buttons), or from a terminal.

**Practise now with the built-in DEMO strategy** (the 9:20 NIFTY short straddle, SL 25% per leg,
always on DEMO prices). `demo` adds it to the Strategy Library the first time:

```bash
python -m marketlens.trading.forward --strategy demo --once --at "2026-10-28 09:18"   # one cycle at a set time
python -m marketlens.trading.forward --strategy demo                                  # every minute, today
```

**Later, use your own strategy:** replace `demo` with its id (shown on page 6, e.g. 3):

```bash
python -m marketlens.trading.forward --strategy 3            # runs every minute in market hours
python -m marketlens.trading.forward --strategy 3 --once     # one cycle, for testing
```

`--at` works with DEMO prices only. The log is in `logs/forward_<id>.log`.

### 10. Receive TradingView / Chartink alerts (module 7)

1. Put a long random secret in `.env` (at least 24 characters). Make one with:

   ```powershell
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```

   and add the line `WEBHOOK_SECRET=<what it printed>` to `.env`.

2. Start the receiver (a second terminal; it runs until `Ctrl + C`):

   ```bash
   python -m marketlens.webhook.server
   ```

   Check it: open http://localhost:8000/health (it answers `{"status": "ok", ...}`).

3. Send a fake alert from PowerShell (replace `YOUR_SECRET` with your `WEBHOOK_SECRET`):

   ```powershell
   $body = @{ secret = "YOUR_SECRET"; strategy = "EMA cross"; symbol = "RELIANCE"; action = "BUY"; qty = 10; price = 2501.5; note = "test alert" } | ConvertTo-Json
   Invoke-RestMethod -Uri "http://localhost:8000/webhook/tradingview" -Method Post -ContentType "application/json" -Body $body
   ```

   The answer is `status: ok` with the new signal's id. Sending it again within 10 seconds answers
   `duplicate`; a wrong secret answers **401**. The event appears on page 7, tab **Webhook alerts**.
   A Chartink-style alert: `-Uri "http://localhost:8000/webhook/chartink?secret=YOUR_SECRET"` with
   `@{ stocks = "RELIANCE,TCS"; trigger_prices = "2501.5,3900.1"; scan_name = "EMA cross" }`.

**Why real TradingView alerts only work after the AWS step:** TradingView sends webhooks from the
internet, so the address must be public, use **port 80 (http) or 443 (https)**, and HTTPS is
strongly recommended so the secret is encrypted on the way. `localhost` is only reachable from your
own computer. On the AWS server (with its static IP, a domain name and an HTTPS certificate) the
same receiver runs behind HTTPS and TradingView can reach it. Until then, test with the fake alerts
above.

---

## Where the data comes from

| Data | Source | Key needed | Used by |
| --- | --- | --- | --- |
| Daily prices of Nifty 200 stocks, ETFs and indices | Yahoo Finance (yfinance), stored in `daily_prices` | No | Signals backtest and scanner (7), momentum ranking (8), ETF portfolio (9), Nifty 50 comparison (10) |
| 1-minute index candles and expired option minutes | Dhan, stored in `price_bars` / `intraday_option_prices` | Dhan | Options backtester (4), simulator (5) |
| Live quotes and option chains | Dhan (not stored) | Dhan | Pages 1, 2 and 6 |
| Security IDs | Dhan's public scrip master (`data/dhan/`) | No | Every Dhan request |
| Stock / ETF lists | `data/universe/*.csv` | No | Downloads, scanner, momentum, ETF page |

Without Dhan keys, the minute data and live quotes are **DEMO** (made up) and every page that shows
them says so. Page **3 → What is stored** lists what is in your database, and
**Settings & Status** shows which keys are missing.

---

## A typical workflow

1. **Download** option minutes for a period (page 3 or `download_options.py`).
2. **Build** a strategy in page 2 (payoff, Greeks) or start from a template in page 4.
3. **Backtest** it on page 4; study the equity curve, heatmaps and exit reasons.
4. **Replay** an interesting day in the Simulator (page 5) and practise trading it.
5. **Save** it to the Strategy Library and **forward-test** it on page 6 with virtual money.
6. Compare forward-test days with the backtest of the same days (page 6, History tab).
7. **Journal** your trades on page 10 (notes, tags, rating, screenshot) and read the stats by tag,
   weekday and strategy to see what works for you.
8. Check your **combined performance** against Nifty 50 on page 10, and reopen, copy or share any
   strategy from the Strategy Library (JSON export / import).

For ETF investing: sync the ETF prices (step 6), backtest on page **9**, check how sensitive the
result is in the **Parameter sweep** tab, compare any claimed return in **Check a claim**, and look
at the **Current signal** after each month end.

---

## Running tests

Tests check every calculation with small hand-made data, so they need no internet and no keys.
With the virtual environment turned on, run this from the MarketLens folder:

```bash
python -m pytest -rs
```

Today: **714 passed, 28 skipped** (the skipped ones are option-pricing cases at the price
limit, where implied volatility cannot be measured). Add `-v` to see each test by name.

- Database tests use **`TEST_DATABASE_URL`**, a separate database that the tests empty and
  refill. They never touch `DATABASE_URL`. If it is empty or unreachable those tests are skipped.
- Page tests run each Streamlit page headless (no browser) with `streamlit.testing`.
- Test files live in `tests/` and their names start with `test_`.

---

## Folder layout

```
MarketLens/
├── app.py                  # main file: builds the sidebar menu
├── dashboards/             # one file per page (home, settings, modules 1-12)
├── marketlens/             # shared code
│   ├── config.py           # reads keys and settings from .env
│   ├── db.py, models.py    # PostgreSQL connection and all tables
│   ├── costs.py            # brokerage, STT, exchange, SEBI, stamp duty, GST, DP charge, slippage (shares, F&O, ETFs)
│   ├── export.py           # CSV / Excel downloads
│   ├── overview.py         # module 1: cards, movers, breadth, sector heatmap
│   ├── utils.py, logger.py, ui.py   # @retry / RateLimiter, logging, shared page pieces
│   ├── broker/             # data clients (Dhan or DEMO), instruments, base.py (Broker interface),
│   │                       # paper.py (PaperBroker: virtual orders and positions)
│   ├── ingest/             # downloads into PostgreSQL: prices, candles, option data;
│   │                       # clean.py (repairs 10x / 100x price errors in Yahoo data)
│   ├── options/            # pricing (Black-Scholes, IV, Greeks), expiries, chain, strategies, strike_select
│   ├── backtest/           # metrics, engine (Trade, BacktestResult), store, charts, report, ui,
│   │                       # options_bt.py (the options backtester and its rules)
│   ├── simulator/          # replay.py (replay clock and virtual trading)
│   ├── trading/            # forward.py (forward-test runner), orders.py (signal -> paper order)
│   ├── signals/            # indicators, rules.py (conditions), backtest.py (backtest, chart, scanner),
│   │                       # pine.py (Pine Script v6 export + validator)
│   ├── webhook/            # payload.py (webhook format, checks, addresses, events), server.py (FastAPI receiver)
│   ├── alerts/             # telegram.py (send a Telegram message)
│   ├── momentum/           # ranking.py (scores, filters, rank on any date), etf_bt.py (ETF rotation backtest)
│   ├── library.py          # module 10: strategy library, trades as round trips, journal, holdings, performance
│   └── scheduler/          # for the upcoming modules
├── migrations/             # Alembic migrations 0001 .. 0009
├── scripts/                # init_db.py, sync_prices.py, update_universe.py, download_options.py
├── tests/                  # automatic tests (python -m pytest)
├── data/universe/          # nifty50.csv, nifty200.csv, etfs.csv
├── data/                   # local downloads, forward-test minutes, journal screenshots (not uploaded)
├── logs/                   # log files (not uploaded)
├── requirements.txt        # packages, each with a comment
└── .env.example            # template for your private keys
```

---

## Upcoming features

The order may change; each item is built with the same rules (keys in `.env` only, paper by
default, DEMO data without keys, tests for every calculation).

### 7. Signals & Indicator Strategy Builder (remaining)
- Forward-testing rules built on this page (module 6 runs EMA-cross / RSI strategies today).
- A public HTTPS address for the webhook receiver (part of the AWS step).

### 8. Momentum Engine Backtest
- ✅ Done: the ranking engine (`marketlens/momentum/ranking.py`). Scores over 63 / 126 / 252
  days (skipping the last 21) by plain return, volatility-adjusted return or Sharpe; filters for
  price above its 200-day average, minimum traded value (Rs crore), biggest one-day move; buys
  only when Nifty 50 is above its 200-day average, else holds LIQUIDBEES or cash. Uses only the
  prices up to the chosen date (tested). Needs Nifty 50 prices: sync `^NSEI` once.
- Next: hold the top N, rebalance monthly or weekly, and the page.
- CAGR, max drawdown, Sharpe, Sortino against Nifty 50, after all costs.

### 9. ETF Momentum Portfolio (remaining)
- Turning the current signal into paper rebalancing orders (and a monthly reminder).

### 10. Personal Analytics & Strategy Library (remaining)
- Saving holdings snapshots from the scheduler as well (today: when the page is opened).
- Live (Dhan) trades in the journal once module 11 places live orders.

### 11. Live Algo Trading & Bot Monitor
- A **Dhan live broker** using the same `Broker` interface as the paper broker, so strategies
  switch from paper to live without code changes, **only when `TRADING_MODE=live`**.
- Risk controls before every order: `MAX_DAILY_LOSS_RS` (stop for the day),
  `MAX_OPEN_POSITIONS`, `MAX_ORDERS_PER_SECOND`, a kill switch.
- Live view of running bots, orders, positions and errors.
- Dhan only accepts orders from a whitelisted static IP, so live trading will run on the server.

### 12. Telegram Bot Control Panel
- Alerts on Telegram: entries, exits, stop losses, daily P&L, errors.
- Commands: status, positions, P&L, stop a strategy, exit all.
- Price alerts ("tell me when NIFTY goes above 25,500").

### Platform
- **Scheduler:** daily price sync, option-data downloads after market close, morning checks.
- **AWS deployment** (Mumbai region, static IP for Dhan orders) with the app and background
  processes running all day.
- **Dhan automatic login** with `DHAN_API_KEY` / `DHAN_API_SECRET` instead of pasting a new
  access token every day.
- Index futures in the backtester; changing lot sizes over history.

### Known limits today
- Forward tests and the simulator need the strategy to be running before the entry time
  (the minutes before the start are not recorded).
- The paper broker's "money used" is simple (quantity × average price); real option-selling
  margin is much higher.
- One lot size is used for a whole backtest (today's lot size unless you set one).
- The ETF backtest applies today's capital gains tax rules to every year (the rules changed in
  2018, 2023 and 2024); surcharge and the 8-year limit on carried-forward losses are not modelled.
- LIQUIDBEES earns a fixed yearly yield you set (5% by default) in the ETF backtest, because Yahoo's
  prices miss part of its daily interest; the real yield moved between about 3% and 6% a year.
- The DP charge (Rs 12.5 + GST per scrip sold from demat) is Dhan's published rate when this was
  written; check it on Dhan's pricing page. It also applies to paper delivery sells.
- Holdings snapshots (page 10) are saved when the page is opened, so a day without a visit has no
  point on the value chart. Day P&L is left blank when yesterday's close is not known.

---

## Adding a new page

1. Create a file in `dashboards/`, for example `dashboards/my_page.py`, starting with
   `import streamlit as st`.
2. Add one line for it in the `pages` list in `app.py`:
   `st.Page("dashboards/my_page.py", title="My Page", icon="✨")`
3. Save. The app reloads and the page appears in the menu.
