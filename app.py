"""MarketLens - main Streamlit app.

Run it from the MarketLens folder with:
    streamlit run app.py

This file only builds the sidebar menu. Each dashboard lives in its own
file inside the "dashboards" folder.
"""
import streamlit as st

# Page title and wide layout for charts. Must be the first Streamlit call.
st.set_page_config(page_title="MarketLens", page_icon="📈", layout="wide")

# Home, Settings and the 12 modules, grouped into sections for the sidebar menu.
# st.Page(file, title=..., icon=...) adds one item to the menu.
pages = {
    "Start": [
        st.Page("dashboards/home.py", title="Home", icon="🏠", default=True),
        st.Page("dashboards/settings.py", title="Settings & Status", icon="⚙️"),
    ],
    "Market & data": [
        st.Page("dashboards/market_overview.py", title="1. Live Market Overview", icon="📊"),
        st.Page("dashboards/option_chain.py", title="2. Option Chain & Strategy Builder", icon="🔗"),
        st.Page("dashboards/options_downloader.py", title="3. Options Data Downloader", icon="📥"),
    ],
    "Options testing": [
        st.Page("dashboards/options_backtester.py", title="4. Options Backtester", icon="🧪"),
        st.Page("dashboards/option_simulator.py", title="5. Option Simulator", icon="⏪"),
        st.Page("dashboards/forward_test.py", title="6. Forward Test / Paper Trading", icon="📝"),
    ],
    "Indicator & portfolio": [
        st.Page("dashboards/signals_builder.py", title="7. Signals & Indicator Strategy Builder", icon="📐"),
        st.Page("dashboards/momentum_engine.py", title="8. Momentum Engine Backtest", icon="🚀"),
        st.Page("dashboards/etf_momentum.py", title="9. ETF Momentum Portfolio", icon="🧺"),
    ],
    "Control centre": [
        st.Page("dashboards/analytics_library.py", title="10. Personal Analytics & Strategy Library", icon="📚"),
        st.Page("dashboards/live_trading.py", title="11. Live Algo Trading & Bot Monitor", icon="🤖"),
        st.Page("dashboards/telegram_panel.py", title="12. Telegram Bot Control Panel", icon="💬"),
    ],
}

# Build the sidebar menu and show the page the user picked
st.sidebar.title("📈 MarketLens")
page = st.navigation(pages)
page.run()
