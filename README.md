# MarketLens

Market Analytics System with Algorithmic Trading Framework for Indian markets (NSE).

MarketLens downloads stock, ETF and options data, shows it in dashboards, backtests
momentum and options strategies, and (later) places orders through the Dhan API
with Telegram alerts, running on an AWS server.

> Everything in this app is historical analysis for learning.
> It is **not** investment advice or a live trading recommendation.

## What works today

The menu has **Home**, **Settings & Status** and 12 modules in 4 groups.

| Group | Module | Status |
| --- | --- | --- |
| Start | Home, Settings & Status (which keys are set, PAPER/LIVE mode) | Ready |
| Market & data | 1. Live Market Overview (index cards, chart with moving averages and crossover signals) | Ready |
| | 2. Option Chain & Strategy Builder | Strategy builder (straddle/strangle payoff) ready; option chain planned |
| | 3. Options Data Downloader | Equity/ETF/index CSV download ready; Dhan options data planned |
| Options testing | 4. Options Backtester, 5. Option Simulator, 6. Forward Test / Paper Trading | Planned |
| Indicator & portfolio | 7. Signals & Indicator Strategy Builder | Pine Script export ready; rules and webhooks planned |
| | 8. Momentum Engine Backtest, 9. ETF Momentum Portfolio | Planned |
| Control centre | 10. Personal Analytics & Strategy Library, 11. Live Algo Trading & Bot Monitor, 12. Telegram Bot Control Panel | Planned |

Planned pages already appear in the menu and list what they will do.

## Setup, step by step (Windows + VS Code)

You need Python 3.11 or newer and Git. Check them in a terminal
(VS Code: menu **Terminal > New Terminal**):

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
# Mac / Linux
source .venv/bin/activate
```

You will see `(.venv)` at the start of the line when it is on.
If PowerShell says scripts are disabled, run this once and try again:
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

### 3. Install the packages

```bash
pip install -r requirements.txt
```

### 4. Add your keys (optional for now)

Copy `.env.example` to a new file called `.env` and fill in your Dhan and
Telegram details (Dhan is the only broker MarketLens uses). Each line in `.env.example` has a comment saying what it is for.
The `.env` file stays on your computer and is never uploaded.
The app works without it: every setting has a safe default (paper trading,
demo mode on), and **Settings & Status** shows which keys are still missing.
Restart the app after changing `.env`.

### 5. Set up the database (PostgreSQL)

MarketLens keeps its data in **PostgreSQL** (17 or newer). Install it from
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

It creates the `marketlens` database if it is missing, runs the Alembic
migrations and prints every table with its row count. It is safe to run again.
If something is wrong (PostgreSQL not running, wrong password) it tells you how to fix it.
**Settings & Status** also has a *Test database connection* button.

When `marketlens/models.py` changes later, make and apply a new migration:

```bash
alembic revision --autogenerate -m "what changed"
alembic upgrade head
```

### 6. Download prices into the database

```bash
python scripts/sync_prices.py --universe etfs --years 10
python scripts/sync_prices.py --universe nifty200 --years 10
```

The first run downloads everything (about 1.5 minutes for Nifty 200 x 10 years);
later runs only fetch the missing days and take seconds. Run it after market
close each day. The stock lists live in `data/universe/` (`nifty50.csv`,
`nifty200.csv`, `etfs.csv`); NSE changes the Nifty lists twice a year, so refresh them with:

```bash
python scripts/update_universe.py
```

### 7. The data downloader page (module 3)

Open **3. Historical Data Downloader** in the app:

| Tab | What it does |
| --- | --- |
| **Latest option chain** | Underlying + expiry: chain with OI, IV, Greeks, PCR. *Save snapshot* stores it; CSV / Excel download |
| **Expired options** | Date range, weekly/monthly, strikes ATM±n, CE/PE: *Download and store* (1-minute bars, progress bar, resumable). Preview any stored day at 1/5/15/25/60 min and export it (or the whole range) as CSV / Excel. **Feeds the Options Backtester and Option Simulator** |
| **Stocks, ETFs & indices** | One ticker, a list or a whole universe (Nifty 50 / 200 / ETFs): free daily prices from yfinance, stored, charted, exported |
| **Intraday candles (Dhan)** | Index / futures / stock candles by Security ID (1, 5, 15, 25, 60 min or daily) into a folder you choose |

Below the tabs, **What is stored** lists every instrument with its first date, last date,
row count and whether it is DEMO data. Without Dhan keys the option and Dhan tabs use
clearly labelled DEMO data.

### 8. Download option data (for the backtester and simulator)

```bash
python scripts/download_options.py index   --underlying NIFTY --from 2026-09-01 --to 2026-10-23
python scripts/download_options.py expired --underlying NIFTY --from 2026-09-01 --to 2026-10-23
python scripts/download_options.py snapshot --underlying NIFTY
```

`expired` stores minute bars for ATM-10 .. ATM+10, calls and puts (change with `--width`,
`--types CE`, `--flag monthly`). Long downloads can be stopped with `Ctrl + C` and run again:
finished days are skipped. Without Dhan keys the same commands store clearly marked DEMO
data; the first real Dhan download deletes it.

### 9. Run the app

```bash
streamlit run app.py
```

Your browser opens at http://localhost:8501. Use the menu on the left to switch
dashboards. Press `Ctrl + C` in the terminal to stop the app.

## Running tests

Tests check the calculations (for example moving averages and crossover
signals) using small made-up price tables, so they need no internet and no keys.
With the virtual environment turned on, run this from the MarketLens folder:

```bash
python -m pytest
```

Add `-v` to see each test by name: `python -m pytest -v`.

Database tests in `tests/test_db.py` use **`TEST_DATABASE_URL`**, a separate
database that the tests empty and refill. They never touch `DATABASE_URL`.
If `TEST_DATABASE_URL` is empty or unreachable those tests are *skipped*;
add `-rs` to see why: `python -m pytest -rs`.
Every line should say `PASSED`. Test files live in `tests/` and their names
start with `test_`.

## Changing the stock or timeframe

On **1. Live Market Overview**, type any NSE ticker followed by `.NS`
(for example `TCS.NS`, `INFY.NS`, `HDFCBANK.NS`) and pick a timeframe
(6 months to 5 years). Indices start with `^`: `^NSEI` is Nifty 50, `^NSEBANK` is Bank Nifty.

## Folder layout

```
MarketLens/
├── app.py                 # main file: builds the sidebar menu
├── dashboards/            # one file per dashboard page
├── marketlens/            # shared code: settings, data download, helpers
│   ├── config.py          # reads keys from .env
│   ├── data.py            # downloads prices, adds moving averages
│   ├── db.py              # PostgreSQL connection, sessions, upsert helper
│   ├── models.py          # the 24 database tables
│   ├── broker/            # market_data.py: get_data_client() -> Dhan (keys set) or DEMO
│   │                      # dhan_data.py (DhanDataClient), demo_data.py (DemoDataClient),
│   │                      # dhan_instruments.py (Security IDs, lot sizes), dhan_client.py (keys, errors)
│   ├── options/           # pricing.py (Black-Scholes), expiries.py (expiry-day rules)
│   ├── ingest/            # prices.py (yfinance daily), historical.py (candles), options.py (option data)
│   ├── costs.py           # brokerage, STT, exchange, SEBI, stamp duty, GST, slippage
│   ├── utils.py           # @retry and RateLimiter helpers
│   ├── logger.py          # writes messages to the terminal and logs/marketlens.log
│   ├── ui.py              # small shared page pieces
│   └── ingest/, options/, backtest/, simulator/, signals/, momentum/,
│       broker/, trading/, alerts/, scheduler/, webhook/   # one folder per area (filled in later)
├── migrations/            # Alembic database migrations (versions/0001_initial_tables.py)
├── scripts/               # init_db.py, sync_prices.py, update_universe.py
├── alembic.ini            # Alembic settings (no password: it reads .env)
├── tests/                 # automatic tests (run with: python -m pytest)
├── data/universe/         # nifty50.csv, nifty200.csv, etfs.csv (symbol lists)
├── data/                  # local CSV downloads (not uploaded)
├── logs/                  # log files (not uploaded)
├── .streamlit/config.toml # app theme
├── requirements.txt       # list of packages
└── .env.example           # template for your private keys
```

## Adding a new dashboard

1. Create a file in `dashboards/`, for example `dashboards/my_page.py`, starting with `import streamlit as st`.
2. Add one line for it in the `pages` list in `app.py`:
   `st.Page("dashboards/my_page.py", title="My Page", icon="✨")`
3. Save. The app reloads and the page appears in the menu.
