# MarketLens

Market Analytics System with an Algorithmic Trading Framework for Indian markets (NSE / BSE),
similar in spirit to AlgoTest and StockMock.

MarketLens downloads index, stock, ETF and options data into PostgreSQL, shows live market
dashboards, builds and backtests option strategies minute by minute, replays past days for
practice, and forward-tests strategies on the live market with virtual money. It also builds
indicator strategies (with TradingView Pine Script export and webhook alerts), backtests an ETF
momentum portfolio after costs and capital gains tax, and keeps every strategy, trade and journal
note in one Strategy Library. It has a live **Dhan** broker behind an order service that checks
risk rules before every order (daily loss kill switch, lot sizes, capital, open positions, Dhan login)
and asks for your Telegram confirmation for live orders; page 11 deploys strategies and monitors them.
Next: a Telegram bot and
an AWS server.

> Everything in this app is historical analysis for learning.
> It is **not** investment advice or a live trading recommendation.

**Safety first:** trading is **paper (virtual money) by default**. Real orders are only possible
with `TRADING_MODE=live` in `.env`, and with `AUTO_TRADE=false` (the default) every live order waits
for your Confirm. Every page works without API keys, using clearly
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
| | 11 | Live Algo Trading & Bot Monitor | Dhan live broker (orders, super orders, books, exit-all) behind safety checks; daily login check and 08:45 Telegram reminder; order service with risk checks, kill switch and Telegram confirmations; live runner and Bot Monitor page (deploy, Start / Pause / Stop, status cards, orders, positions, confirmations, loss limits, error log) | 🛠️ |
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

**11. Live Algo Trading (partly ready)**
- **Dhan live broker:** MARKET / LIMIT / SL / SL-M orders, modify, cancel, super orders (entry + target +
  stop loss), order book, trade book, positions, holdings, funds, exit-all. Every call first checks the
  Dhan keys and `TRADING_MODE=live`; F&O quantities are rounded down to whole lots; tokens never reach the logs.
- **Daily Dhan login:** paste / TOTP / API-key consent / renew, a check that the token lasts until 15:30,
  and a Telegram reminder at 08:45 on weekdays (setup step 11).
- **Order service:** every signal is saved, checked (kill switch, daily and strategy loss, market open,
  Dhan login, lot sizes, capital per trade, open positions, orders per second) and only then sent;
  live orders wait for your Telegram Confirm when `AUTO_TRADE=false` (setup step 13).
- **Kill switch:** switches on when today's loss reaches `MAX_DAILY_LOSS_RS`, blocks new orders for the
  rest of the day (exits still allowed) and, if you chose so, closes every position.
- **Deploy (page 11):** pick a strategy from the library; **live** deployment needs a saved backtest and at
  least 10 forward-test days (the page shows what is missing). Lots multiplier, the strategy's own max loss,
  confirm or automatic orders, and Start / Pause / Stop per strategy.
- **Live runner** (`python -m marketlens.trading.live_runner`): runs every deployed strategy each minute with
  the forward tester's rule code (the same rules as the backtest), but every order goes through the order
  service. A heartbeat every minute; a strategy deployed for paper is paused if `TRADING_MODE` becomes live.
- **Bot Monitor:** a big PAPER (blue) / LIVE (red) banner with AUTO_TRADE and the kill switch; status cards
  (Dhan login, live runner, webhook server, Telegram bot, scheduler, database); running strategies with
  today's P&L, open positions, today's orders, orders waiting for Confirm / Reject; loss-limit progress bars;
  "Kill switch" and "Square off everything" buttons (each with a confirm tick); the error log with filters.
  Refreshes every 10 s in market hours.

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
| `DHAN_CLIENT_ID`, `DHAN_ACCESS_TOKEN` | Live option chain, expired options data, margin and live orders. Dhan is the only broker. The token lasts 24 hours (step 11) | Clearly labelled DEMO data; live orders refused |
| `DHAN_API_KEY`, `DHAN_API_SECRET` (optional) | Daily login through Dhan's consent page instead of pasting a token (step 11) | Use paste or TOTP |
| `DATABASE_URL`, `TEST_DATABASE_URL` | Your PostgreSQL database and a separate test database | Database pages show how to fix it |
| `TRADING_MODE`, `AUTO_TRADE` | `paper` (default) or `live`; `AUTO_TRADE=false` (default) = you confirm every live order | Paper; confirm each order |
| `PAPER_CAPITAL` | Virtual money for the paper broker (default 500000) | Rs 5,00,000 |
| `MAX_DAILY_LOSS_RS`, `MAX_OPEN_POSITIONS`, `MAX_ORDERS_PER_SECOND`, `MAX_CAPITAL_PER_TRADE_RS`, `MAX_STRATEGY_LOSS_RS`, `SQUARE_OFF_ON_KILL`, `CONFIRM_TIMEOUT_MIN` | Risk limits (order service) | Safe defaults |
| `WEBHOOK_SECRET` | Checks TradingView / Chartink alerts sent to the webhook receiver (module 7) | The receiver refuses every alert |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | Webhook alerts (module 7), the 08:45 login reminder, order and kill-switch messages, Confirm / Reject buttons for live orders; the bot later (module 12) | No messages are sent (they are still saved in `notifications`); confirm live orders on the command line |

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
| 0010 | Order status `WAITING_CONFIRMATION` (live orders waiting for your Confirm); `kill_switch_events` |
| 0011 | `live_deployments` (strategies deployed on page 11) and `service_heartbeats` (background processes) |

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

### 11. Daily Dhan login (needs you every morning, because of 2FA)

A Dhan access token lasts **24 hours**. Dhan's login always needs you (password + 2FA, or the code
from your authenticator app), so MarketLens never logs in by itself. **Every trading day before 09:00**,
do ONE of these:

| Way | What you do | Command |
| --- | --- | --- |
| Paste a web token | web.dhan.co → My Profile → Access DhanHQ APIs → generate an Access Token, copy it | `python -m marketlens.broker.session paste` |
| PIN + TOTP (TOTP switched on in Dhan) | Type your PIN and the 6-digit code from your authenticator app (never saved) | `python -m marketlens.broker.session totp` |
| API key + secret (`DHAN_API_KEY`, `DHAN_API_SECRET` in `.env`, valid 12 months) | Open the printed Dhan page, log in with 2FA, paste the `tokenId` from the address bar | `python -m marketlens.broker.session consent` |
| Renew yesterday's web token (before it expires) | Nothing else | `python -m marketlens.broker.session renew` |

Then check it (it must say `active`) and restart the app and background processes:

```bash
python -m marketlens.broker.session check
```

Each new token is tested first and then written to the `DHAN_ACCESS_TOKEN` line of `.env` only (the
other lines stay as they are). Every check is saved in the `broker_sessions` table. The token is never
written to a log. `python -m marketlens.broker.session steps` prints these steps again.

**08:45 Telegram reminder** (needs `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`): on weekdays it checks
the login and sends the steps above if the token is missing, expired, or expires before 15:30. Keep it
running in its own terminal:

```bash
python -m marketlens.broker.session remind --daily
```

or let the computer start it at 08:45 (it checks once and stops):

```powershell
# Windows (run once in PowerShell; use the full path of your MarketLens folder)
schtasks /Create /TN "MarketLens Dhan login reminder" /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 08:45 /TR "C:\Users\YOU\MarketLens\.venv\Scripts\python.exe -m marketlens.broker.session remind" /F
```

```bash
# Ubuntu (crontab -e; the server clock must be on India time, or change 45 8 to UTC 15 3)
45 8 * * 1-5 cd /home/ubuntu/MarketLens && .venv/bin/python -m marketlens.broker.session remind
```

On Windows, Task Scheduler starts in `C:\Windows\System32`: set **Start in** to your MarketLens folder
in the task's Actions tab, so `.env` is found.

### 12. Live trading (only when you are ready)

Orders go through `get_broker()` (`marketlens/broker/factory.py`): the **paper broker** unless
`TRADING_MODE=live` in `.env`, then the **Dhan broker** (`marketlens/broker/dhan_broker.py`). The Dhan
broker can place, modify and cancel MARKET / LIMIT / SL / SL-M orders, place **super orders** (entry +
target + stop loss, Dhan's replacement for bracket orders), read the order book, trade book, positions,
holdings and funds, and **exit all**. Before **every** call it checks that the Dhan keys are set and
`TRADING_MODE=live`; otherwise nothing is sent. F&O quantities are rounded **down** to whole lots
(less than one lot is refused), symbols are matched to Dhan's Security ID with the instrument master,
and live orders are also saved in the `orders` table (mode `live`) for the journal (page 10).

Dhan only accepts orders from your **whitelisted static IP** (web.dhan.co → Access DhanHQ APIs),
which is why live trading is meant to run on the AWS server. Webhook "auto" orders (module 7) stay
paper-only until they are moved onto the order service below.

### 13. The order service (risk checks before every order)

Strategies send signals through `submit_signal()` (`marketlens/trading/order_service.py`). It saves the
signal, runs the risk checks and only then sends the order through `get_broker()`. Every leg must pass,
otherwise **no** leg is sent:

| Rule | Setting in `.env` |
| --- | --- |
| Market open (09:15-15:30, Monday to Friday) | — |
| Dhan login valid until 15:30 today (live only) | — (see step 11) |
| F&O quantity is a whole number of lots | — |
| Money one new order may use (qty × price) | `MAX_CAPITAL_PER_TRADE_RS` |
| Positions open at the same time | `MAX_OPEN_POSITIONS` |
| One strategy's loss today (a strategy's own `max_loss_rs` wins) | `MAX_STRATEGY_LOSS_RS` |
| Total loss today → **kill switch** | `MAX_DAILY_LOSS_RS`, `SQUARE_OFF_ON_KILL` |
| Orders per second | `MAX_ORDERS_PER_SECOND` |

Orders that only reduce or close a position skip the money rules, so you can always get out. When the
daily loss limit is hit, the kill switch blocks every new order for the rest of the day (and closes all
positions if `SQUARE_OFF_ON_KILL=true`). In **live** mode with `AUTO_TRADE=false` the order waits as
`WAITING_CONFIRMATION` and Telegram asks you to Confirm / Reject (cancelled after `CONFIRM_TIMEOUT_MIN`).
Until the Telegram bot (module 12) answers the buttons, use the command line:

```bash
python -m marketlens.trading.order_service status
python -m marketlens.trading.order_service confirm 12     # 12 = the signal number in the message
python -m marketlens.trading.order_service reject 12
python -m marketlens.trading.order_service kill --square-off
python -m marketlens.trading.order_service unkill
```

Every step is written to `logs/marketlens.log`, orders to `orders`, messages to `notifications` and
errors to `error_log`. Run `alembic upgrade head` once (migrations 0010 and 0011).

### 14. Deploy a strategy and run the live runner (page 11)

1. Forward-test the strategy on page 6 for at least **10 market days** and save a backtest of it
   (both are needed for LIVE; PAPER deployment is allowed at once).
2. Page **11 → Deploy**: pick it, choose the lots multiplier, its max loss for the day and *Confirm each on
   Telegram* or *Automatic* (automatic also needs `AUTO_TRADE=true`), then **Deploy**.
3. Start the runner once (the page has a button, or keep a terminal open):

```bash
python -m marketlens.trading.live_runner           # every deployed strategy, every minute
python -m marketlens.trading.live_runner --once    # one cycle, to try it
```

The runner writes `logs/live_runner.log` and checks in every minute (the *Live runner* card turns red
when it stops). **Pause** = no new orders, positions stay; **Stop** = that strategy's positions are closed,
then it stops. In live confirm mode, stop-loss and target exits also wait for your Confirm, and an exit
that comes before you confirmed its entry cancels that entry. A strategy cannot be deployed while its
forward test runs, because both use the same paper account.

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
9. Only when you are ready: do the daily Dhan login (step 11), set `TRADING_MODE=live` and keep
   `AUTO_TRADE=false`, so every order passes the risk checks and waits for your Confirm (steps 12-13).

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

Today: **781 passed, 28 skipped** (the skipped ones are option-pricing cases at the price
limit, where implied volatility cannot be measured). Add `-v` to see each test by name.

- Database tests use **`TEST_DATABASE_URL`**, a separate database that the tests empty and
  refill. They never touch `DATABASE_URL`. If it is empty or unreachable those tests are skipped.
- Page tests run each Streamlit page headless (no browser) with `streamlit.testing`.
- Test files live in `tests/` and their names start with `test_`.

**Save the results as CSV** (for reports or a research paper):

```bash
python scripts/test_report.py
```

It runs every test and writes into `reports/tests/` (date and time in each name, so earlier runs
are kept): `test_results_<when>.csv` (one row per test: file, test, what it checks, status,
seconds, message), `test_summary_<when>.csv` (totals per test file and an ALL row) and
`test_run_<when>.csv` (date, Python, operating system, git commit, totals).

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
│   │                       # paper.py (PaperBroker), dhan_broker.py (live Dhan orders), factory.py
│   │                       # (paper unless TRADING_MODE=live), session.py (daily login, 08:45 reminder)
│   ├── ingest/             # downloads into PostgreSQL: prices, candles, option data;
│   │                       # clean.py (repairs 10x / 100x price errors in Yahoo data)
│   ├── options/            # pricing (Black-Scholes, IV, Greeks), expiries, chain, strategies, strike_select
│   ├── backtest/           # metrics, engine (Trade, BacktestResult), store, charts, report, ui,
│   │                       # options_bt.py (the options backtester and its rules)
│   ├── simulator/          # replay.py (replay clock and virtual trading)
│   ├── trading/            # forward.py (forward-test runner), orders.py (webhook signal -> paper order),
│   │                       # order_service.py (risk checks, kill switch, confirmations),
│   │                       # live_runner.py (deployed strategies), monitor.py (page 11 data)
│   ├── signals/            # indicators, rules.py (conditions), backtest.py (backtest, chart, scanner),
│   │                       # pine.py (Pine Script v6 export + validator)
│   ├── webhook/            # payload.py (webhook format, checks, addresses, events), server.py (FastAPI receiver)
│   ├── alerts/             # telegram.py (send a Telegram message)
│   ├── momentum/           # ranking.py (scores, filters, rank on any date), etf_bt.py (ETF rotation backtest)
│   ├── library.py          # module 10: strategy library, trades as round trips, journal, holdings, performance
│   └── scheduler/          # for the upcoming modules
├── migrations/             # Alembic migrations 0001 .. 0011
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
- Live fills in the journal: live orders are saved today (mode `live`), but their fills are not yet
  read back from Dhan's trade book.

### 11. Live Algo Trading & Bot Monitor
- ✅ Done: the **Dhan live broker** (same `Broker` interface as the paper broker, chosen by
  `get_broker()` only when `TRADING_MODE=live`), the daily login check and the 08:45 reminder.
- ✅ Done: the **order service** with risk checks before every order, the kill switch and
  Telegram Confirm / Reject for live orders (setup step 13).
- ✅ Done: the **live runner** and the **Bot Monitor page** (setup step 14).
- Webhook "auto" orders (module 7) running through the order service.
- Fills read back from Dhan's trade book, so live P&L per strategy uses real fill prices (today the runner
  counts its own strategies at the live price when the order was sent; manual live trades are not split
  per strategy).
- Dhan only accepts orders from a whitelisted static IP, so live trading will run on the server.

### 12. Telegram Bot Control Panel
- Alerts on Telegram: entries, exits, stop losses, daily P&L, errors.
- Commands: status, positions, P&L, stop a strategy, exit all; answering the Confirm / Reject buttons
  (today: `python -m marketlens.trading.order_service confirm <signal>`).
- Price alerts ("tell me when NIFTY goes above 25,500").

### Platform
- **Scheduler:** daily price sync, option-data downloads after market close, morning checks.
- **AWS deployment** (Mumbai region, static IP for Dhan orders) with the app and background
  processes running all day.
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
- The order service's market-open rule knows weekends but not exchange holidays (the broker rejects
  those orders). Paper P&L for the loss limits uses the last saved prices of each position.
- If one leg of a multi-leg order is rejected after another was sent, you get a warning; the sent leg
  is not undone automatically.
- The daily Dhan login always needs you (2FA): MarketLens reminds you but never logs in by itself.

---

## Adding a new page

1. Create a file in `dashboards/`, for example `dashboards/my_page.py`, starting with
   `import streamlit as st`.
2. Add one line for it in the `pages` list in `app.py`:
   `st.Page("dashboards/my_page.py", title="My Page", icon="✨")`
3. Save. The app reloads and the page appears in the menu.
