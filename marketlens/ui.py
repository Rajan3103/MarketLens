"""Small building blocks shared by the dashboard pages."""
import streamlit as st


def historical_note():
    """Show the warning that every analysis page carries (requirement NFR-13)."""
    st.caption(
        "Historical analysis for learning only. Past signals and backtest results "
        "are not a live trading recommendation."
    )


def coming_soon(module: str, planned: list[str]):
    """Placeholder for a dashboard that is designed but not built yet."""
    st.info(f"**{module}** is planned for a later review. It will include:")
    for item in planned:
        st.markdown(f"- {item}")
