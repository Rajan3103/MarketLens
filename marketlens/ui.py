"""Small building blocks shared by the dashboard pages."""
import streamlit as st

from marketlens import auth, config
from marketlens.auth import password_ok  # noqa: F401  (kept here: older code imports it from ui)
from marketlens.costs import COST_FIELDS, CostSettings, cost_settings_from_dict


# Show the warning that every backtest, simulator and signal page carries.
def historical_note():
    """Show the learning disclaimer (requirement NFR-13)."""
    st.caption("Historical analysis for learning, not a live trading recommendation.")


# A short "what this page does / how to read it" note under a page title (two lines, joined by a line break).
def help_note(text: str):
    """Example: help_note("What this page does: ...\\nHow to read it: ...")"""
    st.info(text.strip().replace("\n", "  \n"), icon="ℹ️")      # two spaces + line break = a new line in Markdown


# ---------------------------------------------------------------- dashboard password

# The visitor's IP address (for the wrong-password limit), or "local" when Streamlit does not know it.
def _visitor_address() -> str:
    try:
        address = st.context.ip_address
    except Exception:
        address = None
    return address if isinstance(address, str) and address else "local"


# With DASHBOARD_PASSWORD set: show a password box and stop until it is right; add Logout to the sidebar.
def require_password() -> bool:
    """Returns True when the app may be shown. The login is remembered in st.session_state (this browser tab)."""
    if not config.is_set(config.DASHBOARD_PASSWORD):
        return True
    state = st.session_state
    if state.get("logged_in"):
        if st.sidebar.button("🔒 Logout", key="logout"):
            state.clear()
            st.rerun()
        return True
    st.title("🔒 MarketLens")
    who = _visitor_address()
    wait = auth.LIMITER.wait_seconds(who)
    if wait > 0:
        st.error(f"Too many wrong passwords. Try again in {wait:.0f} seconds.")
        st.stop()
    with st.form("login"):
        typed = st.text_input("Password", type="password", key="login_password")
        sent = st.form_submit_button("Log in", type="primary")
    if sent:
        if auth.password_ok(typed):
            auth.LIMITER.reset(who)
            state["logged_in"] = True
            state.pop("login_password", None)
            st.rerun()
        auth.LIMITER.wrong(who)
        st.error("Wrong password.")
    st.caption("The password is DASHBOARD_PASSWORD in .env.")
    st.stop()
    return False


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

    The boxes, defaults and checks come from COST_FIELDS / cost_settings_from_dict in marketlens/costs.py,
    so the web app offers exactly the same ones. key: a short unique name per page (e.g. "backtester"), so
    two pages don't share the same input boxes.
    """
    f = {field["name"]: field for field in COST_FIELDS}
    with st.expander("💸 Trading costs (brokerage, taxes, slippage)"):
        c1, c2, c3 = st.columns(3)
        values = {
            "brokerage_per_order": c1.number_input(_label(f["brokerage_per_order"]), min_value=0.0,
                                                   value=f["brokerage_per_order"]["default"], key=f"{key}_brok"),
            "brokerage_cap": c2.checkbox(f["brokerage_cap"]["label"], value=f["brokerage_cap"]["default"],
                                         key=f"{key}_cap", help=f["brokerage_cap"]["help"]),
            "delivery_brokerage": c3.number_input(_label(f["delivery_brokerage"]), min_value=0.0,
                                                  value=f["delivery_brokerage"]["default"], key=f"{key}_deliv")}
        s1, s2, s3 = st.columns(3)
        choices = f["slippage_type"]["choices"]
        slip_type = s1.radio(f["slippage_type"]["label"], list(choices), horizontal=True, key=f"{key}_sliptype",
                             format_func=choices.get)
        values["slippage_type"] = slip_type
        values["slippage_value"] = s2.number_input(
            "Slippage (% of price)" if slip_type == "pct" else "Slippage (Rs per unit)", min_value=0.0,
            value=f["slippage_value"]["default"], step=f["slippage_value"]["step"], key=f"{key}_slip")
        values["include_taxes"] = s3.checkbox(f["include_taxes"]["label"], value=f["include_taxes"]["default"],
                                              key=f"{key}_tax")
    return cost_settings_from_dict(values)


# "Brokerage per order (Rs)": a box's label with its unit.
def _label(field: dict) -> str:
    return f"{field['label']} ({field['unit']})" if field["unit"] else field["label"]
