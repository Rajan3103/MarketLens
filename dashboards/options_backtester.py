"""Module 4 - Options Backtester. Similar in spirit to AlgoTest.

1. Settings: underlying, dates, expiry, entry / exit time, days of the week
2. Legs: add, duplicate, remove; strike rule, SL, target, trailing and re-entry per leg
3. Strategy level: range breakout, overall M2M stop / target, costs
4. Ready templates, or the strategy sent from the Strategy builder (module 2)
5. Run on the option minutes stored in PostgreSQL (module 3 downloads them) and show the results
6. Save to the Strategy Library, open a day in the Simulator, start a forward test
"""
import datetime as dt
from dataclasses import asdict

import pandas as pd
import streamlit as st

from marketlens.backtest import options_bt as ob
from marketlens.backtest.ui import show_backtest
from marketlens.ui import cost_settings_inputs, historical_note, key_notice

UNDERLYINGS = ["NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX"]
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri"]
NEW_LEG = asdict(ob.LegSpec("SELL", "CE"))
VALUE_LABEL = {"ATM": "N strikes from ATM", "PREMIUM": "Premium Rs", "DELTA": "Delta"}
VALUE_DEFAULT = {"ATM": 0.0, "PREMIUM": 50.0, "DELTA": 0.20}
VALUE_STEP = {"ATM": 1.0, "PREMIUM": 5.0, "DELTA": 0.05}


# ---------------------------------------------------------------- page state

# Put settings into the form. The version number changes the input boxes' keys, so they show the new values.
def load_into_form(settings: ob.OptionsBacktestSettings):
    st.session_state["bt_form"] = {k: getattr(settings, k) for k in ob.OptionsBacktestSettings.__dataclass_fields__
                                   if k not in ("legs", "costs", "name")}
    kind, quick = ob.quick_kind_of(settings.legs)
    st.session_state["bt_form"].update({"quick_kind": kind, **quick})
    st.session_state["bt_legs"] = []
    for leg in sorted(settings.legs, key=lambda x: x.option_type) if kind != "custom" else settings.legs:
        add_leg(asdict(leg))
    st.session_state["bt_ver"] = st.session_state.get("bt_ver", 0) + 1


# Add a leg row (a copy of `values`) with a new id.
def add_leg(values: dict | None = None, after: int | None = None):
    st.session_state["bt_uid"] = st.session_state.get("bt_uid", 0) + 1
    row = {**(values or NEW_LEG), "uid": st.session_state["bt_uid"]}
    legs = st.session_state.setdefault("bt_legs", [])
    legs.insert(len(legs) if after is None else after + 1, row)


# Strategy type changed: straddle / strangle / premium use exactly one SELL CE and one SELL PE leg.
def set_quick_kind():
    kind = st.session_state[f"quick_kind_{st.session_state['bt_ver']}"]
    if kind == "custom":
        return
    rows = [(leg, leg_values(leg)) for leg in st.session_state["bt_legs"]]
    kept = []
    for option_type in ("CE", "PE"):
        found = next(((leg, vals) for leg, vals in rows if vals["side"] == "SELL" and vals["option_type"] == option_type),
                     None)
        if found:
            kept.append(found[0])
        else:                                    # a new leg, with the other leg's SL settings when there is one
            other = next((vals for _, vals in rows if vals["side"] == "SELL"), NEW_LEG)
            st.session_state["bt_uid"] = st.session_state.get("bt_uid", 0) + 1
            kept.append({**{k: v for k, v in other.items() if k != "uid"}, "side": "SELL",
                         "option_type": option_type, "uid": st.session_state["bt_uid"]})
    st.session_state["bt_legs"] = kept


# Remove the leg row with this id.
def remove_leg(uid: int):
    st.session_state["bt_legs"] = [leg for leg in st.session_state["bt_legs"] if leg["uid"] != uid]


# Copy a leg row with what is typed in its boxes right now.
def duplicate_leg(uid: int):
    legs = st.session_state["bt_legs"]
    i = next(n for n, leg in enumerate(legs) if leg["uid"] == uid)
    add_leg(leg_values(legs[i]), after=i)


# What a leg row's boxes hold now (the stored values for boxes not drawn yet).
def leg_values(leg: dict) -> dict:
    uid, values = leg["uid"], {k: v for k, v in leg.items() if k != "uid"}
    for field in NEW_LEG:
        key = f"leg_{uid}_{field}"
        if field == "value":
            key = f"leg_{uid}_value_{st.session_state.get(f'leg_{uid}_strike_rule', leg['strike_rule'])}"
        if key in st.session_state:
            values[field] = st.session_state[key]
    return values


# Load a ready template (keeps the chosen underlying and dates).
def use_template():
    form = st.session_state["bt_form"]
    v = st.session_state["bt_ver"]
    try:
        load_into_form(ob.template_settings(st.session_state["bt_template"], st.session_state.get(f"underlying_{v}", form["underlying"]),
                                            st.session_state.get(f"start_{v}", form["start"]),
                                            st.session_state.get(f"end_{v}", form["end"])))
        st.session_state.pop("bt_result", None)
    except ValueError as err:
        st.session_state["bt_error"] = str(err)


# Load the legs sent from the Strategy builder as ATM +/- N rules.
def use_builder_legs(handoff: dict):
    form = st.session_state["bt_form"]
    settings = ob.OptionsBacktestSettings(handoff["underlying"], form["start"], form["end"], ob.legs_from_builder(handoff),
                                          entry_time=form["entry_time"], exit_time=form["exit_time"])
    load_into_form(settings)


# The first and last day with stored option minutes (cached for a minute).
@st.cache_data(ttl=60, show_spinner=False)
def stored_range(underlying: str):
    days = ob.StoredOptionData(underlying).trading_days(dt.date(2015, 1, 1), dt.date.today())
    return (days[0], days[-1], len(days)) if days else None


# ---------------------------------------------------------------- page

st.title("🧪 Options Backtester")
st.caption("Test an option strategy minute by minute on stored expired-options data (like AlgoTest).")
key_notice("Dhan", ["DHAN_CLIENT_ID", "DHAN_ACCESS_TOKEN"],
           "The Options Data Downloader stores labelled DEMO data until then.")

if "bt_form" not in st.session_state:
    try:
        found = stored_range("NIFTY")
    except Exception as err:          # database not reachable
        found = None
        st.error(f"Could not read the database: {err}")
    end = found[1] if found else dt.date.today() - dt.timedelta(days=1)
    start = found[0] if found else end - dt.timedelta(days=90)
    load_into_form(ob.template_settings("920_straddle", "NIFTY", start, end))
if st.session_state.pop("bt_error", None):
    st.error("Could not load that template.")
form, v = st.session_state["bt_form"], st.session_state["bt_ver"]

# ---- templates and the Strategy builder hand-off ----
t1, t2 = st.columns([3, 1])
t1.selectbox("Ready templates", list(ob.TEMPLATES), key="bt_template", format_func=lambda k: ob.TEMPLATES[k]["label"])
t2.button("Load template", on_click=use_template, width="stretch")
handoff = st.session_state.get("handoff")
if handoff and handoff.get("to") == "backtest":
    with st.container(border=True):
        st.success(f"Received from the Strategy builder: **{handoff['name']}** ({handoff['underlying']}, "
                   f"{len(handoff['legs'])} legs, built at spot {handoff['spot']:,.0f}).")
        st.caption("Exact strikes become ATM ± N rules, so each day picks the same distance from that day's ATM.")
        if st.button("Use these legs", key="bt_use_builder"):
            try:
                use_builder_legs(handoff)
                st.session_state.pop("handoff")
                st.rerun()
            except ValueError as err:
                st.error(str(err))

# ---- 1. when and what ----
st.subheader("1. Underlying, dates and timing")
c = st.columns([1.2, 1, 1, 1.1, 1.1, 0.9, 0.9])
underlying = c[0].selectbox("Underlying", UNDERLYINGS, index=UNDERLYINGS.index(form["underlying"]), key=f"underlying_{v}")
start = c[1].date_input("From", form["start"], key=f"start_{v}", format="DD/MM/YYYY")
end = c[2].date_input("To", form["end"], key=f"end_{v}", format="DD/MM/YYYY")
expiry_type = c[3].radio("Expiry", ["weekly", "monthly"], index=["weekly", "monthly"].index(form["expiry_type"]),
                         key=f"expiry_type_{v}", horizontal=True)
expiry_which = c[4].radio("Which expiry", ["current", "next"], index=["current", "next"].index(form["expiry_which"]),
                          key=f"expiry_which_{v}", horizontal=True)
entry_time = c[5].time_input("Entry", form["entry_time"], key=f"entry_time_{v}", step=60)
exit_time = c[6].time_input("Exit", form["exit_time"], key=f"exit_time_{v}", step=60)
d = st.columns([0.6] * 5 + [1.6, 2])
weekdays = tuple(i for i, name in enumerate(DAYS)
                 if d[i].checkbox(name, i in form["weekdays"], key=f"day_{i}_{v}"))
skip_expiry_day = d[5].checkbox("Skip expiry day", form["skip_expiry_day"], key=f"skip_{v}")
try:
    found = stored_range(underlying)
    d[6].caption(f"📦 Stored {underlying} option days: {found[2]} ({found[0]:%d-%b-%Y} to {found[1]:%d-%b-%Y})"
                 if found else f"📦 No {underlying} option minutes stored yet.")
except Exception:
    found = None

# ---- 2. legs ----
st.subheader("2. Legs")
kinds = list(ob.QUICK_KINDS)
kind = st.radio("Strategy type", kinds, index=kinds.index(form.get("quick_kind", "custom")), horizontal=True,
                format_func=ob.QUICK_KINDS.get, key=f"quick_kind_{v}", on_change=set_quick_kind)
quick = kind != "custom"
ce_offset, pe_offset = form.get("ce_offset", 2), form.get("pe_offset", 2)
ce_premium, pe_premium = form.get("ce_premium", 100.0), form.get("pe_premium", 100.0)
with st.container(border=True):
    if kind == "straddle":
        st.caption("**Short straddle:** sells the ATM call and the ATM put at the entry time each day.")
    elif kind == "strangle":
        q1, q2, q3 = st.columns([1, 1, 2])
        ce_offset = q1.selectbox("CE strike", ob.STRANGLE_OFFSETS, index=ob.STRANGLE_OFFSETS.index(ce_offset),
                                 format_func=lambda n: f"ATM+{n}", key=f"q_ce_offset_{v}")
        pe_offset = q2.selectbox("PE strike", ob.STRANGLE_OFFSETS, index=ob.STRANGLE_OFFSETS.index(pe_offset),
                                 format_func=lambda n: f"ATM-{n}", key=f"q_pe_offset_{v}")
        q3.caption(f"**Short strangle:** sells the call {ce_offset} strike(s) above ATM and the put {pe_offset} "
                   "strike(s) below ATM (both out of the money), using ATM at the entry time each day. "
                   "One strike = the strike gap (NIFTY 50 points, BANKNIFTY 100).")
    elif kind == "premium":
        q1, q2, q3 = st.columns([1, 1, 2])
        ce_premium = q1.number_input("CE premium (Rs)", min_value=0.05, value=float(ce_premium), step=5.0,
                                     key=f"q_ce_premium_{v}")
        pe_premium = q2.number_input("PE premium (Rs)", min_value=0.05, value=float(pe_premium), step=5.0,
                                     key=f"q_pe_premium_{v}")
        q3.caption(f"**Premium based:** at the entry time each day, sells the call whose price is nearest "
                   f"Rs {ce_premium:g} and the put whose price is nearest Rs {pe_premium:g} (prices known at that "
                   "minute; only strikes traded in the last 5 minutes). If two strikes are equally near, the one "
                   "further from ATM is used.")
    else:
        st.caption("**Custom legs:** any number of legs, buy or sell, with ATM ± N, premium or delta strikes.")
st.caption("SL, target, trailing and lock use the leg's unit (% of entry price, price points, or Rs on the whole leg). "
           "0 means off. Trailing: for every X in your favour, move the SL by Y.")
legs_values = []
for n, leg in enumerate(st.session_state["bt_legs"], start=1):
    uid = leg["uid"]
    with st.container(border=True):
        r = st.columns([0.55, 0.9, 0.8, 0.8, 1.4, 1.1, 0.45, 0.45])
        r[0].markdown(f"**Leg {n}**")
        if quick:                                        # the strategy type sets side, type and strike
            vals = leg_values(leg)
            rule, value = ob.quick_strike(kind, vals["option_type"], ce_offset, pe_offset, ce_premium, pe_premium)
            r[1].markdown("SELL")
            r[2].markdown(vals["option_type"])
            r[3].number_input("Lots", 1, 500, int(leg["lots"]), key=f"leg_{uid}_lots")
            r[4].markdown(f"Strike: **{'ATM' if rule == 'ATM' and not value else ''}"
                          f"{f'ATM{int(value):+d}' if rule == 'ATM' and value else ''}"
                          f"{f'premium ≈ Rs {value:g}' if rule == 'PREMIUM' else ''}**")
            r[5].caption("set by the strategy type above")
        else:
            r[1].selectbox("Side", ["SELL", "BUY"], index=["SELL", "BUY"].index(leg["side"]), key=f"leg_{uid}_side")
            r[2].selectbox("Type", ["CE", "PE"], index=["CE", "PE"].index(leg["option_type"]),
                           key=f"leg_{uid}_option_type")
            r[3].number_input("Lots", 1, 500, int(leg["lots"]), key=f"leg_{uid}_lots")
            rule = r[4].selectbox("Strike", list(ob.STRIKE_RULES), index=list(ob.STRIKE_RULES).index(leg["strike_rule"]),
                                  format_func=ob.STRIKE_RULES.get, key=f"leg_{uid}_strike_rule")
            value = float(leg["value"]) if rule == leg["strike_rule"] else VALUE_DEFAULT[rule]
            r[5].number_input(VALUE_LABEL[rule], value=value, step=VALUE_STEP[rule], key=f"leg_{uid}_value_{rule}",
                              format="%.0f" if rule == "ATM" else "%.2f")
            r[6].button("⧉", key=f"leg_{uid}_dup", help="Duplicate this leg", on_click=duplicate_leg, args=(uid,))
            r[7].button("✕", key=f"leg_{uid}_del", help="Remove this leg", on_click=remove_leg, args=(uid,))
        x = st.columns([1.3, 0.8, 0.8, 0.8, 0.8, 0.8, 0.8, 1.6, 0.8])
        x[0].selectbox("Unit", list(ob.UNITS), index=list(ob.UNITS).index(leg["unit"]), format_func=ob.UNITS.get,
                       key=f"leg_{uid}_unit")
        for col, field, label in ((x[1], "sl", "SL"), (x[2], "target", "Target"), (x[3], "trail_x", "Trail every X"),
                                  (x[4], "trail_y", "move SL by Y"), (x[5], "lock_at", "Lock at"),
                                  (x[6], "lock_to", "lock profit")):
            col.number_input(label, min_value=0.0, value=float(leg[field] or 0), step=1.0, key=f"leg_{uid}_{field}")
        x[7].selectbox("After SL", list(ob.REENTRY), index=list(ob.REENTRY).index(leg["reentry"]),
                       format_func=ob.REENTRY.get, key=f"leg_{uid}_reentry")
        x[8].number_input("Max re-entries", 0, 20, int(leg["max_reentries"]), key=f"leg_{uid}_max_reentries")
    values = leg_values(leg)
    if quick:
        values["side"] = "SELL"
        values["strike_rule"], values["value"] = ob.quick_strike(kind, values["option_type"], ce_offset, pe_offset,
                                                                 ce_premium, pe_premium)
    legs_values.append(values)
if not quick:
    st.button("➕ Add leg", on_click=add_leg)

# ---- 3. strategy level ----
st.subheader("3. Strategy settings")
s1, s2 = st.columns(2)
with s1.container(border=True):
    entry_mode = st.radio("Entry", ["time", "breakout"], index=["time", "breakout"].index(form["entry_mode"]),
                          key=f"entry_mode_{v}", horizontal=True,
                          format_func=lambda m: "At the entry time" if m == "time" else "Range breakout")
    if entry_mode == "breakout":
        b = st.columns(4)
        range_start = b[0].time_input("Range from", form["range_start"], key=f"range_start_{v}", step=60)
        range_end = b[1].time_input("Range to", form["range_end"], key=f"range_end_{v}", step=60)
        range_source = b[2].selectbox("Range of", ["underlying", "leg"], index=["underlying", "leg"].index(form["range_source"]),
                                      key=f"range_source_{v}",
                                      format_func=lambda s: "the index (all legs)" if s == "underlying" else "each leg's price")
        breakout_side = b[3].selectbox("Enter on a break of", ["high", "low", "either"],
                                       index=["high", "low", "either"].index(form["breakout_side"]), key=f"side_{v}")
        st.caption("The range is recorded from the first to the second time; the strategy then enters when a "
                   "candle breaks it (the exit time still applies).")
    else:
        range_start, range_end, range_source, breakout_side = (form["range_start"], form["range_end"],
                                                               form["range_source"], form["breakout_side"])
    move_sl_to_cost = st.checkbox("After any leg's SL, move the other legs' SL to cost", form["move_sl_to_cost"],
                                  key=f"cost_{v}")
with s2.container(border=True):
    st.markdown("**Overall M2M (whole strategy, Rs, before costs)** · 0 = off")
    m = st.columns(3)
    m2m = {}
    for col, field, label in ((m[0], "m2m_sl", "M2M stop loss"), (m[1], "m2m_target", "M2M target"),
                              (m[2], "m2m_lock_at", "Lock when profit reaches"), (m[0], "m2m_lock_to", "…keep at least"),
                              (m[1], "m2m_trail_x", "Trail: every Rs X more"), (m[2], "m2m_trail_y", "raise the stop by Y")):
        m2m[field] = col.number_input(label, min_value=0.0, value=float(form[field] or 0), step=500.0,
                                      key=f"{field}_{v}") or None
k1, k2 = st.columns(2)
capital = k1.number_input("Capital (Rs, for return %, CAGR and drawdown %)", 10_000.0, 1e9, float(form["capital"]),
                          step=50_000.0, key=f"capital_{v}")
lot_size = k2.number_input(f"Lot size (0 = today's {underlying} lot)", 0, 10_000, 0, key=f"lot_{v}")
costs = cost_settings_inputs("backtester")

# ---- 5. run ----
settings, problem = None, None
try:
    settings = ob.OptionsBacktestSettings(
        underlying, start, end, [ob.leg_from_values(x) for x in legs_values], expiry_type=expiry_type,
        expiry_which=expiry_which, entry_time=entry_time, exit_time=exit_time, weekdays=weekdays,
        skip_expiry_day=skip_expiry_day, lot_size=lot_size or None, capital=capital, costs=costs,
        entry_mode=entry_mode, range_start=range_start, range_end=range_end, range_source=range_source,
        breakout_side=breakout_side, move_sl_to_cost=move_sl_to_cost, **m2m)
except ValueError as err:
    problem = str(err)

if problem:
    st.error(f"Fix this before running: {problem}")
elif st.button("▶️ Run backtest", type="primary", key="bt_run"):
    try:
        stored = ob.stored_day_count(underlying, start, end)
    except Exception as err:
        stored = None
        st.error(f"Could not read the database: {err}")
    if stored == 0:
        st.warning(f"No {underlying} option minutes are stored for {start:%d-%b-%Y} to {end:%d-%b-%Y}. "
                   "Download them first (module 3), then run again.")
        st.session_state["bt_no_data"] = True
    elif stored:
        st.session_state.pop("bt_no_data", None)
        bar = st.progress(0.0, text="Starting…")

        # Move the progress bar after each day.
        def progress(done: int, total: int):
            bar.progress(done / total, text=f"Day {done} of {total}")

        try:
            st.session_state["bt_result"] = ob.run_options_backtest(settings, progress=progress)
            st.session_state["bt_settings"] = settings
            bar.empty()
        except Exception as err:
            bar.empty()
            st.error(f"The backtest stopped: {err}")
if st.session_state.get("bt_no_data"):
    if st.button("📥 Open the Options Data Downloader", key="bt_to_downloader"):
        st.switch_page("dashboards/options_downloader.py")

# ---- results ----
result = st.session_state.get("bt_result")
if result is None:
    historical_note()
    st.stop()

st.divider()
ran = st.session_state["bt_settings"]
extras = result.extras
st.caption(f"Run {'#' + str(extras['run_id']) + ' saved' if extras.get('run_id') else 'not saved'} · "
           f"{extras['days_traded']} days traded · {len(extras['skipped'])} days skipped")
if len(extras["skipped"]):
    with st.expander(f"Skipped days ({len(extras['skipped'])}) and why"):
        st.dataframe(extras["skipped"], hide_index=True, width="stretch")
if result.trades:
    reasons = pd.Series([t.exit_reason for t in result.trades]).value_counts()
    st.markdown("**Why trades exited:** " + " · ".join(f"{ob.EXIT_REASONS.get(r, r)} {n}" for r, n in reasons.items()))
show_backtest(result, key="bt")
if len(extras["mtm"]):
    with st.expander("Each day's best and worst M2M (before costs)"):
        st.dataframe(extras["mtm"], hide_index=True, width="stretch")

# ---- 6. use it ----
st.subheader("Use this strategy")
u1, u2, u3 = st.columns(3)
with u1.container(border=True):
    name = st.text_input("Strategy name", ran.name[:80], key="bt_save_name")
    if st.button("💾 Save to Strategy Library", key="bt_save"):
        try:
            sid = ob.save_backtest_strategy(name, ran)
            st.success(f"Saved '{name}' (id {sid}). It will appear in module 10, Strategy Library.")
        except Exception as err:
            st.error(f"Could not save: {err}")
with u2.container(border=True):
    days = list(extras["mtm"]["date"]) if len(extras["mtm"]) else []
    day = st.selectbox("Day", days, format_func=lambda x: f"{x:%a %d-%b-%Y}", key="bt_sim_day",
                       index=len(days) - 1 if days else None)
    if st.button("⏪ Open this day in the Simulator", key="bt_sim", disabled=not days):
        row = extras["mtm"][extras["mtm"]["date"] == day].iloc[0]
        trades = result.trades_frame()
        st.session_state["sim_handoff"] = {"underlying": ran.underlying, "day": day, "expiry": row["expiry"],
                                           "name": ran.name, "settings": ran.to_params(),
                                           "trades": trades[trades["entry_time"].map(lambda t: t.date()) == day]}
        st.switch_page("dashboards/option_simulator.py")
with u3.container(border=True):
    st.caption("Run the same rules on today's live market with virtual money.")
    if st.button("📝 Start forward test", key="bt_forward"):
        st.session_state["handoff"] = {"to": "paper", "source": "Options Backtester", "name": ran.name,
                                       "underlying": ran.underlying, "settings": ran.to_params(),
                                       "legs": [{"leg": leg.describe(), **asdict(leg)} for leg in ran.legs]}
        st.switch_page("dashboards/forward_test.py")
