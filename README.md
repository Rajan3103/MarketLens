```console
╔══════════════════════════════════════════════════════════════════════════════════════════╗
║  ┌──(marketlens㉿nse)-[~/algo-desk]                                                      ║
║  └─$ ./launch_marketlens.sh --market NSE --mode PAPER --broker DHAN --version 0.11.0     ║
║                                                                                          ║
║  ███╗   ███╗ █████╗ ██████╗ ██╗  ██╗███████╗████████╗██╗     ███████╗███╗   ██╗███████╗  ║
║  ████╗ ████║██╔══██╗██╔══██╗██║ ██╔╝██╔════╝╚══██╔══╝██║     ██╔════╝████╗  ██║██╔════╝  ║
║  ██╔████╔██║███████║██████╔╝█████╔╝ █████╗     ██║   ██║     █████╗  ██╔██╗ ██║███████╗  ║
║  ██║╚██╔╝██║██╔══██║██╔══██╗██╔═██╗ ██╔══╝     ██║   ██║     ██╔══╝  ██║╚██╗██║╚════██║  ║
║  ██║ ╚═╝ ██║██║  ██║██║  ██║██║  ██╗███████╗   ██║   ███████╗███████╗██║ ╚████║███████║  ║
║  ╚═╝     ╚═╝╚═╝  ╚═╝╚═╝  ╚═╝╚═╝  ╚═╝╚══════╝   ╚═╝   ╚══════╝╚══════╝╚═╝  ╚═══╝╚══════╝  ║
║                                                                                          ║
║  [SYSTEM INITIALIZED]: MarketLens — Market Analytics & Algorithmic Trading Framework     ║
║                        for Indian markets (NSE / BSE). Paper by default.                 ║
╚══════════════════════════════════════════════════════════════════════════════════════════╝
```

<div align="center">

[![Python](https://img.shields.io/badge/🐍_Core-Python_3.13-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![Streamlit](https://img.shields.io/badge/📊_Dashboard-Streamlit_1.66-FF4B4B?style=for-the-badge&logo=streamlit&logoColor=white)](https://streamlit.io/)
[![FastAPI](https://img.shields.io/badge/⚡_Backend-FastAPI_0.162-009688?style=for-the-badge&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Next.js](https://img.shields.io/badge/⚛_Frontend-Next.js_16_React_19-000000?style=for-the-badge&logo=nextdotjs&logoColor=white)](https://nextjs.org/)
[![PostgreSQL](https://img.shields.io/badge/🐘_Database-PostgreSQL_18-4169E1?style=for-the-badge&logo=postgresql&logoColor=white)](https://www.postgresql.org/)
[![Dhan](https://img.shields.io/badge/🏦_Broker-Dhan_API_v2-6C3FC5?style=for-the-badge)](https://dhanhq.co/docs/v2/)
[![Telegram](https://img.shields.io/badge/🔔_Alerts-Telegram_Bot-26A5E4?style=for-the-badge&logo=telegram&logoColor=white)](https://core.telegram.org/bots)
[![Tests](https://img.shields.io/badge/🧪_Tests-948_passed-2EA44F?style=for-the-badge&logo=pytest&logoColor=white)](#-test_suite--quality-gates)
[![Mode](https://img.shields.io/badge/🛡_Mode-PAPER_by_default-1565C0?style=for-the-badge)](#-safety_protocols--risk-controls)

</div>

---

### 🖥 `SYS_DIAGNOSTICS // SYSTEM STATUS`

```console
$ python -m marketlens.scheduler --list && python -m pytest -q
[BOOT]        MarketLens v0.11.0  ·  Python 3.13  ·  PostgreSQL 18  ·  Asia/Kolkata
[MODULES]     12 / 12 READY      (Streamlit app: all 12 · Web app: modules 1, 8, 11)
[DATABASE]    31 tables  ·  Alembic migrations 0001 → 0016
[BROKER]      Dhan API v2        → live orders behind risk checks  (PAPER unless TRADING_MODE=live)
[MARKET_DATA] Dhan quotes / option chains · yfinance daily prices (DEMO data when keys are missing)
[SERVICES]    API · live runner · scheduler (10 jobs) · Telegram bot · price-alert watcher
[TESTS]       948 passed · 28 skipped · network and real database never touched
[STATUS]      ✅ OPERATIONAL — historical analysis for learning, not a live trading recommendation
```

---

### ⚡ `CAPABILITIES // CORE MODULES`

```console
📊 MARKET & DATA
[MOD-01] 📈 Live Market Overview
         └─ Nifty / Bank Nifty / Fin Nifty / Sensex / VIX cards, Nifty 50 movers & breadth, sector heatmap,
            price chart with 20/50/200-day averages, RSI, Bollinger, volume (auto-refresh in market hours)
[MOD-02] 🔗 Option Chain & Strategy Builder
         └─ PCR, max pain, ATM straddle, expected move, OI charts · 12 templates, payoff today / at expiry,
            net Greeks, probability of profit, margin
[MOD-03] 📥 Historical Data Downloader
         └─ Chain snapshots, expired options (1-min, ATM ± 10), daily prices, Dhan intraday candles, resume

🧪 OPTIONS TESTING
[MOD-04] 🧪 Options Backtester
         └─ Minute-by-minute multi-leg tests: ATM ± N / premium / delta strikes, leg SL / target / trailing /
            lock, re-entry, range breakout, overall M2M · equity, drawdown, heatmaps, Excel report
[MOD-05] ⏪ Option Simulator
         └─ Replay a past day 1x–30x with the chain as it was; virtual orders with SL / target and costs
[MOD-06] 📝 Forward Test / Paper Trading
         └─ A saved strategy runs every minute on today's market with virtual money (background process)

📐 INDICATOR & PORTFOLIO
[MOD-07] 📐 Signals & Indicator Strategy Builder
         └─ AND / OR rules on SMA, EMA, RSI, MACD, Bollinger, Supertrend, VWAP, ATR · scanner ·
            TradingView Pine Script v6 export · TradingView / Chartink webhook alerts
[MOD-08] 🚀 Momentum Engine Backtest vs Nifty
         └─ Top-N Nifty 50 / 200 stocks, hold-while-in-top-M buffer, equal or inverse-volatility weights,
            NIFTYBEES (with dividends) benchmark, Yahoo price-error repair · 10 years in ~6 s
[MOD-09] 🧺 ETF Momentum Portfolio
         └─ Top-K ETF rotation after costs and Indian capital gains tax · claim checker · parameter sweep

🎛 CONTROL CENTRE
[MOD-10] 📚 Personal Analytics & Strategy Library
         └─ Every strategy, holding, trade and journal note · combined performance vs Nifty 50
[MOD-11] 🤖 Live Algo Trading & Bot Monitor
         └─ Deploy (live needs a backtest + 10 forward-test days), Start / Pause / Stop, status cards,
            orders, Confirm / Reject, loss limits, kill switch, square off, error log
[MOD-12] 💬 Telegram Bot Control Panel
         └─ /status /positions /pnl /pause /stop /squareoff … · notification switches · price alerts
            (price, % change, indicator rule, OI, PCR) · every message saved (sent / failed / skipped)
```

---

### 🧩 `ARCHITECTURE // HOW IT FITS`

```console
                ┌──────────────────────┐        ┌─────────────────────────┐
   you ───────▶ │ Streamlit  :8501     │        │ Next.js web app  :3000  │ ◀─────── you
                │ app.py · 12 pages    │        │ frontend/ (React 19)    │
                └──────────┬───────────┘        └────────────┬────────────┘
                           │ Python                          │ /api/* /webhook/* (proxy)
                           ▼                                 ▼
                ┌───────────────────────────────────────────────────────────┐
                │ marketlens/  (engines, risk, costs, tax, data cleaning)   │
                │  ├─ FastAPI  :8000  routes · background jobs · webhooks   │
                │  ├─ live runner      every deployed strategy, each minute │
                │  ├─ scheduler        10 jobs (APScheduler, Asia/Kolkata)  │
                │  └─ Telegram bot     commands · Confirm / Reject buttons  │
                └───────┬──────────────────┬──────────────────┬─────────────┘
                        ▼                  ▼                  ▼
                ┌───────────────┐  ┌───────────────┐  ┌────────────────────┐
                │ PostgreSQL 18 │  │ Dhan API v2   │  │ yfinance · Telegram │
                │ 31 tables     │  │ orders · data │  │ prices  · messages  │
                └───────────────┘  └───────────────┘  └────────────────────┘
```

---

### 🛡 `SAFETY_PROTOCOLS // RISK CONTROLS`

```console
[GUARD-01] 🔐 Keys only in .env (git-ignored) · never in code, logs, job records or API answers
[GUARD-02] 📄 PAPER by default · real orders only with TRADING_MODE=live
[GUARD-03] ✋ AUTO_TRADE=false → every live order waits for your ✅ Confirm (Telegram or the web page)
[GUARD-04] 🧮 Order service checks every order: market hours, Dhan login, lot sizes, capital per trade,
           open positions, per-strategy loss, rate limit
[GUARD-05] 🛑 Kill switch at MAX_DAILY_LOSS_RS (optional automatic square-off) · manual kill / square off
           need a tick box AND a second click
[GUARD-06] 🚦 Live deploy only with a saved backtest + 10 forward-test days
[GUARD-07] 🔒 DASHBOARD_PASSWORD for the Streamlit app (hmac.compare_digest, logout, retry pause)
[GUARD-08] 🧪 No look-ahead in every backtest (tested) · costs, slippage and Indian tax included
[GUARD-09] 🏷 DEMO data is always labelled, and never mixed with real prices
```

---

### 📁 `DIRECTORY_TREE // REPOSITORY MAP`

```console
MarketLens/
├── 📄 app.py                    # Streamlit entry: password gate + sidebar menu
├── 📁 dashboards/               # Home, Settings & Status, modules 1–12 (one page each)
├── 📁 marketlens/               # all shared Python code
│   ├── 📄 config.py             # reads .env (keys, mode, limits)
│   ├── 📄 models.py · db.py     # 31 PostgreSQL tables · connection
│   ├── 📄 costs.py              # brokerage, STT, exchange, SEBI, stamp, GST, DP charge, slippage
│   ├── 📁 broker/               # Dhan data + orders, paper broker, daily login
│   ├── 📁 ingest/               # prices, candles, options; clean.py (Yahoo error repair)
│   ├── 📁 options/              # Black-Scholes, IV, Greeks, chain maths, strategies
│   ├── 📁 backtest/             # options backtester, metrics, charts, Excel report
│   ├── 📁 signals/              # indicators, rules, Pine Script export
│   ├── 📁 momentum/             # ranking, portfolio (costs + tax), stock & ETF backtests
│   ├── 📁 trading/              # order service, forward test, live runner, monitor
│   ├── 📁 alerts/               # telegram.py, bot.py, price_alerts.py
│   ├── 📁 api/                  # FastAPI app, routes/, background jobs
│   ├── 📁 webhook/              # TradingView / Chartink receiver
│   └── 📁 scheduler/            # jobs.py (10 jobs) · python -m marketlens.scheduler
├── 📁 frontend/                 # Next.js 16 web app (app/, components/, lib/)
├── 📁 migrations/               # Alembic 0001 … 0016
├── 📁 scripts/                  # init_db, sync_prices, update_universe, download_options, test_report
├── 📁 tests/                    # pytest suite (948 tests)
├── 📁 data/                     # universe lists, nse_holidays.csv (downloads & backups git-ignored)
├── 📁 docs/GUIDE.md             # the full step-by-step guide
├── 📄 requirements.txt          # every package with a comment
└── 📄 .env.example              # template for your private keys
```

---

### 🚀 `QUICKSTART // RUNNING MARKETLENS`

#### 1️⃣ Clone & Configure Environment

```bash
$ git clone https://github.com/Rajan3103/MarketLens.git
$ cd MarketLens
$ python -m venv venv
$ venv\Scripts\activate            # Windows      (Ubuntu: source venv/bin/activate)
$ pip install -r requirements.txt
$ copy .env.example .env           # Windows      (Ubuntu: cp .env.example .env)
# [OUTPUT]: fill DATABASE_URL first; every other key is optional (pages show DEMO data without them)
```

#### 2️⃣ Create the Database & Load Prices (PostgreSQL 18)

```bash
$ python scripts/init_db.py                                        # creates the database + all tables
$ alembic upgrade head                                             # later: bring tables up to date
$ python scripts/sync_prices.py --universe nifty200 --years 12 --backfill
$ python scripts/sync_prices.py --universe etfs --years 10
# [OUTPUT]: Done: 200 symbols: 523,037 rows saved, ...  (about 2 minutes the first time)
```

#### 3️⃣ Launch the Dashboard (Streamlit)

```bash
$ streamlit run app.py
# [OUTPUT]: Local URL: http://localhost:8501   → Home, Settings & Status, modules 1–12
```

#### 4️⃣ Launch the API + Web App (Node 18+)

```bash
$ python -m marketlens.api                  # terminal 1 → http://127.0.0.1:8000/docs
$ cd frontend && npm install && npm run dev # terminal 2 → http://localhost:3000
```

#### 5️⃣ Background Services (leave running)

```bash
$ python -m marketlens.scheduler                    # 10 daily jobs: prices, login check, reports, backup
$ python -m marketlens.trading.live_runner          # runs every deployed strategy (add --once to test)
$ python -m marketlens.alerts.bot                   # Telegram commands + Confirm / Reject buttons
$ python -m marketlens.alerts.price_alerts watch    # price alerts every minute in market hours
```

> 📖 Every step in detail (Dhan daily login, live trading, BotFather, webhooks, frontend): **[docs/GUIDE.md](docs/GUIDE.md)**

---

### 🔑 `CONFIG // .ENV KEYS`

```bash
# ---- required ----
DATABASE_URL=postgresql+psycopg://marketlens:CHANGE_ME@localhost:5432/marketlens
TEST_DATABASE_URL=postgresql+psycopg://marketlens:CHANGE_ME@localhost:5432/marketlens_test

# ---- trading (safe defaults) ----
TRADING_MODE=paper            # paper | live
AUTO_TRADE=false              # false = you confirm every live order
MAX_DAILY_LOSS_RS=2000        # kill switch level · also MAX_OPEN_POSITIONS, MAX_CAPITAL_PER_TRADE_RS ...

# ---- optional: unlock real data, alerts and orders ----
DHAN_CLIENT_ID=               # live chain, intraday data, orders (token lasts 24 h)
DHAN_ACCESS_TOKEN=
TELEGRAM_BOT_TOKEN=           # @BotFather (see docs/GUIDE.md step 16)
TELEGRAM_CHAT_ID=
WEBHOOK_SECRET=               # TradingView / Chartink alerts (24+ characters)
DASHBOARD_PASSWORD=           # password box before the Streamlit menu
API_PORT=8000
FRONTEND_URL=http://localhost:3000
PG_DUMP_PATH=                 # only if pg_dump is not found by itself
```

---

### ⏰ `SCHEDULER // DAILY JOBS (IST)`

```console
08:30  Mon–Fri   refresh Nifty 50 / 200 lists + Dhan instrument master
08:45  Mon–Fri   check today's Dhan login → Telegram reminder if not valid
09:00  Mon–Fri   sync daily prices (stocks, ETFs, Nifty 50, Momentum 30 ETFs)
09:20  1st trading day of the month   momentum + ETF rebalance signals → Telegram
*/5    09:15–15:30   NIFTY / BANKNIFTY option chain snapshots (Dhan)
*/1    09:15–15:30   price alerts
15:45  Mon–Fri   today's 1-minute option data (NIFTY, BANKNIFTY, FINNIFTY, SENSEX, ATM ± 10)
15:50  Mon–Fri   end-of-day report → Telegram (P&L, forward vs backtest, top movers)
16:00  Mon–Fri   holdings snapshot
10:00  Sunday    pg_dump backup → data/backups (newest 8 kept)
# NSE holidays from data/nse_holidays.csv are skipped · every run is shown on Settings & Status
```

---

### 📊 `DB_SCHEMA // DATA MODEL`

```sql
-- 31 tables · PostgreSQL 18 · Alembic 0001 → 0016
-- MARKET DATA     instruments · daily_prices · price_bars · option_contracts · option_quotes
--                 intraday_option_prices · download_progress
-- STRATEGIES      strategies · strategy_legs · backtest_runs · backtest_trades · forward_test_runs
--                 simulator_sessions · simulator_trades · portfolio_holdings · holdings_snapshots
-- TRADING         signals · orders · positions · live_deployments · kill_switch_events · broker_sessions
-- ALERTS          price_alerts · notifications · webhook_events
-- OPERATIONS      background_jobs · scheduler_runs · service_heartbeats · app_settings · error_log
-- JOURNAL         trade_journal

-- orders (main columns; every order, paper or live, from every page and runner)
CREATE TABLE orders (
    id               BIGSERIAL    PRIMARY KEY,
    mode             VARCHAR      NOT NULL DEFAULT 'paper',     -- paper | live
    signal_id        BIGINT       REFERENCES signals(id)    ON DELETE SET NULL,
    strategy_id      INTEGER      REFERENCES strategies(id) ON DELETE SET NULL,
    symbol           VARCHAR,                                   -- RELIANCE · NIFTY 25000 CE 03-Nov-2026
    side             VARCHAR      NOT NULL,                     -- BUY | SELL
    order_type       VARCHAR      NOT NULL DEFAULT 'MARKET',
    product          VARCHAR      NOT NULL DEFAULT 'INTRADAY',
    qty              INTEGER      NOT NULL,
    status           VARCHAR      NOT NULL DEFAULT 'PENDING',   -- FILLED · REJECTED · WAITING_CONFIRMATION ...
    broker_order_id  VARCHAR,                                   -- Dhan's id (live)
    placed_at        TIMESTAMPTZ  NOT NULL DEFAULT now()
);
```

---

### 💻 `REST & WEBHOOK API // ENDPOINT CHEATSHEET`

```http
GET    /api/health                          --> status, version, database, PAPER / LIVE, DEMO
GET    /api/settings/status                 --> which keys are set (never their values)
GET    /api/market/snapshot                 --> index cards, movers, breadth (background job)
GET    /api/market/sectors?window=1 week    --> sector heatmap
GET    /api/market/history?symbol=TCS.NS    --> candles, averages, RSI, Bollinger, crossovers, chart
GET    /api/market/history/download         --> CSV / Excel
GET    /api/jobs/{job_id}                   --> progress, message, result of a background job
POST   /api/jobs/{job_id}/cancel            --> stop it at its next step
GET    /api/momentum/options                --> default settings and choices
POST   /api/momentum/data-check             --> missing prices, history warning
POST   /api/momentum/backtest               --> background job → full report with run_id
POST   /api/momentum/sync-prices            --> download prices (older history too)
GET    /api/momentum/ranks                  --> today's ranks (historical model output, not advice)
GET    /api/momentum/runs/{id}/rebalances   --> buys and sells on each rebalance
POST   /api/momentum/strategies             --> save to the Strategy Library
GET    /api/live/status                     --> PAPER / LIVE banner + status cards
POST   /api/live/deploy                     --> deploy (lots, max loss, auto / confirm)
POST   /api/live/deployments/{id}/status    --> running | paused | stopping
GET    /api/live/monitor                    --> running strategies, positions, orders, waiting orders
POST   /api/live/signals/{id}/confirm       --> send a waiting live order (or /reject)
POST   /api/live/kill  {"confirm":"KILL"}   --> kill switch ON (also /unkill)
POST   /api/live/square-off                 --> close everything, stop every strategy
GET    /api/live/errors?level=ERROR         --> error log with filters
POST   /webhook/tradingview                 --> TradingView alert → signal (WEBHOOK_SECRET required)
POST   /webhook/chartink                    --> Chartink scan alert
# Every route with "Try it out": http://127.0.0.1:8000/docs · errors are always {"error": "..."}
```

---

### 🧪 `TEST_SUITE // QUALITY GATES`

```bash
$ python -m pytest                       # 948 passed, 28 skipped
$ python -m pytest tests/test_momentum_portfolio.py -v
$ python scripts/test_report.py          # results as CSV + XML in reports/tests/ (for the research paper)
$ cd frontend && npm run lint && npm run build
# [OUTPUT]: tests never use the internet or the real database (TEST_DATABASE_URL only)
```

---

### 🗺 `ROADMAP // PENDING`

```console
[ ] Web app versions of modules 2, 3, 4, 5, 6, 7, 9, 10, 12 (all ready in Streamlit)
[ ] Login for the web app and a key check on the API (the Streamlit app already has a password)
[ ] Point-in-time Nifty 200 membership for the momentum engine (removes survivorship bias)
[ ] Paper-trade the monthly momentum / ETF picks automatically
[ ] AWS deployment: services, HTTPS for webhooks, static IP for Dhan live orders
[ ] CI on GitHub Actions (tests on every push) · ruff for Python linting
[ ] Next year's NSE holiday list each December (data/nse_holidays.csv)
```

---

### ⚠️ `DISCLAIMER // READ FIRST`

```console
MarketLens is a learning and research project. Backtests, signals, ranks and reports are historical
analysis for learning, not a live trading recommendation. Trading in equities and derivatives carries
risk of loss. Keep TRADING_MODE=paper until you understand every rule, and never trade money you cannot
afford to lose.
```

---

### 📄 `LICENSE // ALL RIGHTS RESERVED`

```console
No open-source license has been chosen yet, so all rights are reserved by the author.
MarketLens © 2026 Karthig Rajan S. Built with Python, Streamlit, FastAPI, Next.js, PostgreSQL, Dhan API & Telegram.
```
