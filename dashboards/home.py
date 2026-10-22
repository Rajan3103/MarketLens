"""Home page: what MarketLens is and how to use the menu."""
import streamlit as st

st.title("📈 MarketLens")
st.subheader("Market Analytics System with Algorithmic Trading Framework")

st.markdown(
    """
Welcome! Pick a dashboard from the menu on the left.

| Section | What you will find |
| --- | --- |
| **Overview** | Live market picture, price charts with moving averages, data downloads |
| **Options** | Option chain, straddle and strangle payoffs, premium-based strategies |
| **Momentum** | Momentum stock portfolios and ETF rotation, compared with Nifty 50 |
| **Tools** | TradingView Pine Script generator and your own dashboard |
| **Trading** | Paper and live orders, Telegram alerts, settings |
"""
)

st.info(
    "Dashboards marked *planned* are designed in the Review 1 documents and "
    "will be built in later reviews."
)
st.caption(
    "Everything here is historical analysis for learning. "
    "It is not investment advice or a live trading recommendation."
)
