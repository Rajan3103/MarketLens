"""Small building blocks shared by the dashboard pages."""
import streamlit as st

from marketlens import config


# Show the warning that every backtest, simulator and signal page carries.
def historical_note():
    """Show the learning disclaimer (requirement NFR-13)."""
    st.caption("Historical analysis for learning, not a live trading recommendation.")


# Show a placeholder box for a module that is designed but not built yet.
def coming_soon(module: str, planned: list[str]):
    """List what the module will do once it is built."""
    st.info(f"**{module}** is planned for a later review. It will include:")
    for item in planned:
        st.markdown(f"- {item}")


# Show a green PAPER or red LIVE banner from TRADING_MODE in .env.
def trading_mode_banner():
    """Remind the user whether orders use virtual or real money."""
    if config.TRADING_MODE == "live":
        st.error("Mode: LIVE. Orders use real money.")
    else:
        st.success("Mode: PAPER. Orders use virtual money only. "
                   "Set TRADING_MODE=live in .env to change.")


# Tell the user whether the keys a page needs are filled in.
def key_notice(service: str, names: list[str], without_it: str):
    """Show 'key not set' with the missing key names, or a short 'connected' note.

    names: setting names from config, e.g. ["DHAN_CLIENT_ID", "DHAN_ACCESS_TOKEN"]
    without_it: what the page does when the keys are missing
    """
    missing = [name for name in names if not config.is_set(getattr(config, name))]
    if missing:
        st.warning(f"🔑 {service} key not set ({', '.join(missing)}). {without_it} "
                   "Add the key to .env and restart the app.")
    else:
        st.caption(f"🔑 {service} keys are set.")
