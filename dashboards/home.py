"""Home page: what MarketLens is and how to use the menu."""
import streamlit as st

from marketlens.ui import help_note

st.title("📈 MarketLens")
st.subheader("Market Analytics System with Algorithmic Trading Framework")
help_note("What this app does: market data, option and indicator backtests, paper and live trading, alerts and "
          "reports for NSE / BSE, one module per page in the menu on the left.\n"
          "How to read it: every module below is ready; pages that need the Dhan keys show clearly labelled DEMO "
          "data until you add them (Settings & Status shows which keys are set).")

# One row per module: (group, number, name, what it does). All twelve are ready.
MODULES = [
    ("Market & data", 1, "Live Market Overview",
     "Nifty, Bank Nifty, Fin Nifty, Sensex, VIX cards; Nifty 50 gainers / losers and breadth; sector heatmap; "
     "price chart with averages, RSI and volume (auto-refresh in market hours)"),
    ("", 2, "Option Chain & Strategy Builder",
     "Chain with PCR, max pain, straddle, expected move, OI charts; builder with 12 templates, payoff today / at "
     "expiry, net Greeks, POP, margin, save / backtest / paper trade (DEMO without Dhan keys)"),
    ("", 3, "Historical Data Downloader",
     "Option chain snapshots, expired options (1-min, ATM ± 10), stocks / ETFs / indices daily, Dhan intraday "
     "candles; what is stored (DEMO without Dhan keys)"),
    ("Options testing", 4, "Options Backtester",
     "Multi-leg strategies on stored 1-min option data: ATM ± N / premium / delta strikes, leg SL / target / "
     "trailing / lock, re-entry, range breakout, overall M2M; equity, drawdown, heatmaps, trade list, Excel"),
    ("", 5, "Option Simulator",
     "Replay a stored day minute by minute (play, pause, step, 1x-30x); chain as it was then; virtual orders with "
     "SL / target and costs; P&L curve, payoff, saved sessions"),
    ("", 6, "Forward Test / Paper Trading",
     "Run a saved options or indicator strategy every minute with virtual money as a background process; open "
     "legs, trades with exit reasons; day by day vs the backtest"),
    ("Indicator & portfolio", 7, "Signals & Indicator Strategy Builder",
     "AND / OR rules on SMA, EMA, RSI, MACD, Bollinger, Supertrend, VWAP, ATR; backtest with markers; scanner; "
     "Pine Script v6 export; webhook alerts"),
    ("", 8, "Momentum Engine Backtest",
     "Top-N Nifty 50 / 200 momentum portfolio vs Nifty 50 after costs and tax; current ranks; rebalance history "
     "(in the web app)"),
    ("", 9, "ETF Momentum Portfolio",
     "Top-K ETF rotation after costs and tax vs Nifty 50; check a claimed return; parameter sweep"),
    ("Control centre", 10, "Personal Analytics & Strategy Library",
     "Strategy library, holdings & P&L, trade journal, combined performance vs Nifty 50"),
    ("", 11, "Live Algo Trading & Bot Monitor",
     "Deploy strategies (live needs a backtest + 10 forward-test days), live runner, risk checks, kill switch, "
     "confirmations, status cards, loss limits, error log (paper by default)"),
    ("", 12, "Telegram Bot Control Panel",
     "Bot commands and Confirm / Reject buttons, notification switches, price alerts, message history"),
]

rows = "\n".join(f"| {f'**{group}**' if group else ''} | {num} | {name} | {what} | ✅ Ready |"
                 for group, num, name, what in MODULES)
st.markdown("Pick a dashboard from the menu on the left.\n\n"
            "| Group | # | Module | What it does | Status |\n| --- | --- | --- | --- | --- |\n" + rows)

st.info("Use **Settings & Status** to see which keys are set, whether trading is in PAPER or LIVE mode, and the "
        "scheduled jobs.")
st.caption("Historical analysis for learning, not a live trading recommendation.")
