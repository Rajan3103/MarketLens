"""Home page: what MarketLens is and how to use the menu."""
import streamlit as st

st.title("📈 MarketLens")
st.subheader("Market Analytics System with Algorithmic Trading Framework")

st.markdown(
    """
Welcome! Pick a dashboard from the menu on the left.
✅ = ready to use, 🛠️ = partly ready, 📋 = planned.

| Group | # | Module | What it does | Status |
| --- | --- | --- | --- | --- |
| **Market & data** | 1 | Live Market Overview | Nifty, Bank Nifty, Fin Nifty, Sensex, VIX cards; Nifty 50 gainers/losers and breadth (auto-refresh in market hours); moving-average chart | ✅ |
| | 2 | Option Chain & Strategy Builder | Chain with PCR, max pain, straddle, expected move, OI charts; builder with 12 templates, payoff today/at expiry, net Greeks, POP, margin, save/backtest/paper trade | ✅ (DEMO without Dhan keys) |
| | 3 | Historical Data Downloader | Option chain snapshots, expired options (1-min, ATM±10), stocks/ETFs/indices daily, Dhan intraday candles; what is stored | ✅ (DEMO without Dhan keys) |
| **Options testing** | 4 | Options Backtester | Multi-leg strategies on stored 1-min option data: ATM±N / premium / delta strikes, leg SL/target/trailing/lock, re-entry, range breakout, overall M2M; 5 templates; equity, drawdown, day/month heatmaps, trade list with exit reasons, Excel | ✅ (on stored data) |
| | 5 | Option Simulator | Replay a stored day minute by minute (play, pause, step, 1x-30x, jump, slider); chain with LTP/IV/delta/OI as it was then; virtual orders with SL/target, exit all, costs; P&L curve, payoff, summary; saved sessions | ✅ (on stored data) |
| | 6 | Forward Test / Paper Trading | Run a saved options (backtester rules) or indicator strategy every minute on live Dhan / DEMO prices through the paper broker, as a background process; status, open legs, trades with exit reasons; history vs backtest | ✅ (paper only) |
| **Indicator & portfolio** | 7 | Signals & Indicator Strategy Builder | Condition builder (AND/OR) on SMA, EMA, RSI, MACD, Bollinger, Supertrend, VWAP, ATR; backtest with chart markers; scanner on Nifty 50/200, ETFs or a watchlist; Pine Script v6 export with webhook alerts; FastAPI webhook receiver (saves alerts, Telegram, paper orders for auto strategies) | ✅ |
| | 8 | Momentum Engine Backtest | Momentum stock portfolio against Nifty 50 | 📋 |
| | 9 | ETF Momentum Portfolio | Rotate among NSE ETFs by momentum | 📋 |
| **Control centre** | 10 | Personal Analytics & Strategy Library | Saved strategies, results and your P&L analytics | 📋 |
| | 11 | Live Algo Trading & Bot Monitor | Dhan orders (paper by default) and bot status | 📋 |
| | 12 | Telegram Bot Control Panel | Alerts and bot commands on Telegram | 📋 |
"""
)

st.info("Use **Settings & Status** to see which keys are set and whether trading is in PAPER or LIVE mode.")
st.caption("Historical analysis for learning, not a live trading recommendation.")
