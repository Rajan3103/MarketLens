# MarketLens

Market Analytics System with Algorithmic Trading Framework for Indian markets (NSE).

MarketLens downloads stock, ETF and options data, shows it in dashboards, backtests
momentum and options strategies, and (later) places orders through the Dhan API
with Telegram alerts, running on an AWS server.

> Everything in this app is historical analysis for learning.
> It is **not** investment advice or a live trading recommendation.

## What works today

| Menu item | Status |
| --- | --- |
| Home | Ready |
| Market Overview (Nifty, Bank Nifty, VIX, chart with 20/50-day moving averages and crossover signals) | Ready |
| Data Download (any ticker to CSV) | Ready |
| Straddle & Strangle payoff | Ready |
| Pine Script Generator (MA crossover) | Ready |
| Settings & Status (shows which keys are set) | Ready |
| Option Chain, Premium Strategies, Momentum Engine, ETF Momentum, Custom Dashboard, Orders, Alerts | Planned (page and menu in place) |

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
Telegram details. The `.env` file stays on your computer and is never uploaded.
The ready pages work without it.

### 5. Run the app

```bash
streamlit run app.py
```

Your browser opens at http://localhost:8501. Use the menu on the left to switch
dashboards. Press `Ctrl + C` in the terminal to stop the app.

## Changing the stock or timeframe

On **Market Overview**, type any NSE ticker followed by `.NS`
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
│   └── ui.py              # small shared page pieces
├── data/                  # local database and downloads (not uploaded)
├── .streamlit/config.toml # app theme
├── requirements.txt       # list of packages
└── .env.example           # template for your private keys
```

## Adding a new dashboard

1. Create a file in `dashboards/`, for example `dashboards/my_page.py`, starting with `import streamlit as st`.
2. Add one line for it in the `pages` list in `app.py`:
   `st.Page("dashboards/my_page.py", title="My Page", icon="✨")`
3. Save. The app reloads and the page appears in the menu.
