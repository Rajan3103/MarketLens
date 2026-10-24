"""Small building blocks shared by the dashboard pages."""
import streamlit as st

from marketlens import config
from marketlens.costs import (BROKERAGE_CAP_PCT, BROKERAGE_PER_ORDER_RS, DELIVERY_BROKERAGE_RS,
                              CostSettings)


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


# Show a "Trading costs" box and return the CostSettings the user picked.
def cost_settings_inputs(key: str) -> CostSettings:
    """Used by every backtester, simulator and paper-trading page.

    key: a short unique name per page (e.g. "backtester"), so two pages
    don't share the same input boxes.
    """
    with st.expander("💸 Trading costs (brokerage, taxes, slippage)"):
        c1, c2, c3 = st.columns(3)
        per_order = c1.number_input("Brokerage per order (Rs)", min_value=0.0,
                                    value=BROKERAGE_PER_ORDER_RS, key=f"{key}_brok")
        cap_on = c2.checkbox(f"Cap at {BROKERAGE_CAP_PCT}% (whichever lower)", value=True,
                             key=f"{key}_cap", help="Intraday and futures only")
        delivery = c3.number_input("Delivery brokerage (Rs)", min_value=0.0,
                                   value=DELIVERY_BROKERAGE_RS, key=f"{key}_deliv")
        s1, s2, s3 = st.columns(3)
        slip_type = s1.radio("Slippage in", ["pct", "points"], horizontal=True, key=f"{key}_sliptype",
                             format_func=lambda v: "% of price" if v == "pct" else "Rs points")
        slip_value = s2.number_input("Slippage", min_value=0.0, value=0.0, step=0.01,
                                     key=f"{key}_slip")
        taxes = s3.checkbox("Include taxes (STT, exchange, SEBI, stamp, GST)", value=True,
                            key=f"{key}_tax")
    return CostSettings(brokerage_per_order=per_order,
                        brokerage_cap_pct=BROKERAGE_CAP_PCT if cap_on else None,
                        delivery_brokerage=delivery, slippage_type=slip_type,
                        slippage_value=slip_value, include_taxes=taxes)
