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
| **Market & data** | 1 | Live Market Overview | Nifty, Bank Nifty and VIX cards; price charts with moving averages | ✅ |
| | 2 | Option Chain & Strategy Builder | Live option chain; build strategies and see their payoff | 🛠️ payoff builder ready |
| | 3 | Historical Data Downloader | Index, index futures and stock candles from Dhan (1/5/15/25/60 min, daily) by Security ID; expired options; free daily prices | 🛠️ Dhan downloads ready (DEMO without keys) |
| **Options testing** | 4 | Options Backtester | Test option strategies on past data | 📋 |
| | 5 | Option Simulator | Replay a past day and practise trading it | 📋 |
| | 6 | Forward Test / Paper Trading | Run a strategy live with virtual money | 📋 |
| **Indicator & portfolio** | 7 | Signals & Indicator Strategy Builder | Indicator rules, TradingView/Chartink webhooks, Pine Script | 🛠️ Pine export ready |
| | 8 | Momentum Engine Backtest | Momentum stock portfolio against Nifty 50 | 📋 |
| | 9 | ETF Momentum Portfolio | Rotate among NSE ETFs by momentum | 📋 |
| **Control centre** | 10 | Personal Analytics & Strategy Library | Saved strategies, results and your P&L analytics | 📋 |
| | 11 | Live Algo Trading & Bot Monitor | Dhan orders (paper by default) and bot status | 📋 |
| | 12 | Telegram Bot Control Panel | Alerts and bot commands on Telegram | 📋 |
"""
)

st.info("Use **Settings & Status** to see which keys are set and whether trading is in PAPER or LIVE mode.")
st.caption("Historical analysis for learning, not a live trading recommendation.")
