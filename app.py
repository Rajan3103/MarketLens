"""MarketLens - main Streamlit app.

Run it from the MarketLens folder with:
    streamlit run app.py

This file only builds the sidebar menu. Each dashboard lives in its own
file inside the "dashboards" folder.
"""
import streamlit as st

# Page title and wide layout for charts. Must be the first Streamlit call.
st.set_page_config(page_title="MarketLens", page_icon="📈", layout="wide")

# Every dashboard, grouped into sections for the sidebar menu.
# st.Page(file, title=..., icon=...) adds one item to the menu.
pages = {
    "Overview": [
        st.Page("dashboards/home.py", title="Home", icon="🏠", default=True),
        st.Page("dashboards/market_overview.py", title="Market Overview", icon="📊"),
        st.Page("dashboards/data_download.py", title="Data Download", icon="⬇️"),
    ],
    "Options": [
        st.Page("dashboards/option_chain.py", title="Option Chain", icon="🔗"),
        st.Page("dashboards/straddle_strangle.py", title="Straddle & Strangle", icon="🦋"),
        st.Page("dashboards/premium_strategies.py", title="Premium-Based Strategies", icon="💰"),
    ],
    "Momentum": [
        st.Page("dashboards/momentum_engine.py", title="Momentum Engine vs Nifty", icon="🚀"),
        st.Page("dashboards/etf_momentum.py", title="ETF Momentum Portfolio", icon="🧺"),
    ],
    "Tools": [
        st.Page("dashboards/pine_script.py", title="Pine Script Generator", icon="🌲"),
        st.Page("dashboards/custom_dashboard.py", title="Custom Dashboard", icon="🧩"),
    ],
    "Trading": [
        st.Page("dashboards/trading.py", title="Orders & Positions", icon="🤖"),
        st.Page("dashboards/alerts.py", title="Alerts & Telegram", icon="🔔"),
        st.Page("dashboards/settings.py", title="Settings & Status", icon="⚙️"),
    ],
}

# Build the sidebar menu and show the page the user picked
st.sidebar.title("📈 MarketLens")
page = st.navigation(pages)
page.run()
