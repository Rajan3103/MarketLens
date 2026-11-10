"""Module 9 - ETF Momentum Portfolio.

Rotate among NSE ETFs by momentum: hold the top K, rebalance monthly or weekly, park empty slots in
LIQUIDBEES or cash. Backtest after brokerage, taxes, slippage and capital gains tax; check a claimed
return; see how sensitive the result is to K and the lookbacks; see what the strategy holds now.
"""
import datetime as dt

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from marketlens.backtest import charts, store
from marketlens.backtest.report import export_excel, file_stem
from marketlens.costs import CostSettings
from marketlens.momentum import etf_bt as eb
from marketlens.momentum.ranking import METHODS
from marketlens.ui import help_note, historical_note

FUTURE_NOTE = ("Past results depend on the period and the settings chosen. They are not a promise of "
               "future returns.")
METHOD_LABELS = {"return": "Plain return", "vol_adjusted": "Return / volatility", "sharpe": "Sharpe-style"}


# Stored ETF prices (repaired), or made-up DEMO prices when nothing is stored yet.
@st.cache_resource(ttl=600, show_spinner="Loading ETF prices ...")
def get_data() -> tuple[eb.EtfData, str]:
    try:
        return eb.load_etf_data(), ""
    except Exception as err:            # empty database or no connection: the page still works
        return eb.demo_etf_data(), str(err)


# Rupees with commas.
def rs(value) -> str:
    return "-" if value is None or pd.isna(value) else f"Rs {value:,.0f}"


# A warning when no ETF could be bought for a while after the start (too little price history).
def late_start_note(start: dt.date, first_etf_day: dt.date | None):
    if first_etf_day is None:
        st.warning("No ETF passed the rules in this period, so the money stayed in LIQUIDBEES / cash.")
    elif (first_etf_day - start).days > 31:
        st.warning(f"The first ETF was bought only on {first_etf_day:%d-%b-%Y}: before that no ETF had enough "
                   "price history for the longest lookback (or none passed the filters), so the money waited "
                   "in LIQUIDBEES / cash.")


# A percent with 2 decimals.
def pct(value) -> str:
    return "-" if value is None or pd.isna(value) else f"{value:,.2f}%"


st.title("🧺 ETF Momentum Portfolio")
help_note("What this page does: holds the top K ETFs by momentum, rebalanced monthly or weekly, after costs and "
          "Indian capital gains tax, against Nifty 50; also checks a claimed return and sweeps the settings.\n"
          "How to read it: lookbacks in trading days, returns and CAGR in % a year, values in Rs; signals at a "
          "month's (week's) last close, trades at the next day's close.")
st.info(f"**{FUTURE_NOTE}** Historical analysis for learning, not a live trading recommendation.")
data, problem = get_data()
if data.is_demo:
    st.warning("DEMO DATA: no stored ETF prices were found, so made-up prices are used. "
               "Download real ones with `python scripts/sync_prices.py --universe etfs`."
               + (f" ({problem})" if problem else ""))

universe = eb.etf_universe()
first_days = data.first_days()
first_day = data.closes.index[0].date()


# A default date moved inside the stored prices.
def inside(day: dt.date) -> dt.date:
    return min(max(day, first_day), data.last_day)

# ---------------------------------------------------------------- inputs

# Put settings into the form; a new version number gives the input boxes new keys, so they show them.
def load_form(settings: eb.EtfBacktestSettings):
    st.session_state["etf_form"] = settings
    st.session_state["etf_ver"] = st.session_state.get("etf_ver", 0) + 1
    for name in ("etf_result", "etf_settings", "etf_claim_out", "etf_sweep"):
        st.session_state.pop(name, None)


if "etf_form" not in st.session_state:
    load_form(eb.EtfBacktestSettings(symbols=list(data.closes.columns), start=inside(dt.date(2018, 1, 1)),
                                     end=data.last_day))
# A strategy opened from the Strategy Library (module 10)
if (st.session_state.get("handoff") or {}).get("to") == "etf":
    opened = st.session_state.pop("handoff")
    try:
        saved = eb.EtfBacktestSettings.from_params(opened["params"])
        saved.start = inside(saved.start)
        saved.end = inside(saved.end) if saved.end else data.last_day
        load_form(saved)
        st.success(f"Opened **{opened['name']}** from the Strategy Library. Press Run to backtest it.")
    except (ValueError, TypeError, KeyError) as err:
        st.error(f"Could not open '{opened['name']}': {err}")
form, v = st.session_state["etf_form"], st.session_state["etf_ver"]

st.subheader("1. ETFs")
etfs = universe[universe["symbol"].isin(data.closes.columns)].copy()
etfs.insert(0, "Use", etfs["symbol"].isin(form.symbols) | (etfs["symbol"] == eb.LIQUID))
etfs["Tax group"] = etfs["category"].map(eb.tax_class).map(
    {"equity": "Equity", "other": "Gold / silver / international", "debt": "Liquid (slab rate)"})
etfs["Prices from"] = etfs["symbol"].map(first_days)
picked = st.data_editor(etfs, hide_index=True, width="stretch", key=f"etf_pick_{v}",
                        disabled=["symbol", "name", "category", "Tax group", "Prices from"],
                        column_config={"Use": st.column_config.CheckboxColumn("Use", width="small")})
symbols = picked.loc[picked["Use"], "symbol"].tolist()
st.caption("ETFs that listed later (e.g. SILVERBEES in 2022) are ranked only once they have enough history "
           "for every lookback. LIQUIDBEES is never ranked: it is where empty slots wait.")
if data.fixes:
    with st.expander(f"🔧 {len(data.fixes)} price errors in the downloaded data were repaired"):
        st.caption("Yahoo stored a few days of some ETFs at 1/10 or 1/100 of the real price around unit "
                   "splits. Each such day is scaled back using only the price the day before.")
        st.dataframe(pd.DataFrame(data.fixes), hide_index=True)

st.subheader("2. Strategy")
c = st.columns(4)
top_k = c[0].slider("Hold the top K (ETFs)", 1, 5, form.top_k, key=f"etf_k_{v}")
rebalance = c[1].radio("Rebalance", eb.REBALANCE, index=eb.REBALANCE.index(form.rebalance), horizontal=True,
                       key=f"etf_reb_{v}", format_func=str.capitalize,
                       help="Ranked at the close of the last trading day of the month / week; traded at the next close.")
method = c[2].selectbox("Score", METHODS, index=METHODS.index(form.method), format_func=METHOD_LABELS.get,
                        key=f"etf_method_{v}",
                        help="Plain return; return divided by volatility; or Sharpe-style average / volatility")
skip = c[3].number_input("Skip the latest (trading days)", 0, 63, int(form.skip_recent), key=f"etf_skip_{v}",
                         help="Leave out the most recent N trading days from every lookback (21 = one month)")
saved_weights = dict(zip(form.lookbacks, form.weights))
lookbacks = st.multiselect("Lookbacks", list(eb.LOOKBACK_CHOICES),
                           default=[n for n in form.lookbacks if n in eb.LOOKBACK_CHOICES],
                           format_func=eb.LOOKBACK_CHOICES.get, key=f"etf_lb_{v}")
weights = []
if lookbacks:
    wc = st.columns(len(lookbacks))
    for col, days in zip(wc, sorted(lookbacks)):
        weights.append(col.number_input(f"Weight of {eb.LOOKBACK_CHOICES[days]} (relative)", 0.0, 10.0,
                                        float(saved_weights.get(days, 1.0)), 0.5, key=f"etf_w_{days}_{v}"))
a = st.columns(4)
abs_filter = a[0].checkbox("Absolute momentum filter", form.abs_filter, key=f"etf_abs_{v}",
                           help="An ETF is bought only if it has itself gone up over the lookbacks; "
                                "otherwise its slot goes to LIQUIDBEES / cash")
threshold = a[1].number_input("... by more than (%)", -50.0, 100.0, float(form.abs_threshold_pct), 0.5,
                              key=f"etf_abs_thr_{v}", disabled=not abs_filter)
safe = a[2].radio("Empty slots go to", [eb.LIQUID, "CASH"], index=[eb.LIQUID, "CASH"].index(form.safe_asset),
                  horizontal=True, key=f"etf_safe_{v}",
                  format_func=lambda x: "LIQUIDBEES" if x == eb.LIQUID else "Cash (0%)")
band = a[3].number_input("Drift band (%)", 0.0, 50.0, float(form.drift_band_pct), 1.0, key=f"etf_band_{v}",
                         help="An ETF kept from last time is re-weighted only when it is this % away from "
                              "its target weight (fewer small trades, lower costs)")
lq = st.columns(4)
liquid_source = lq[0].radio("LIQUIDBEES return", ["yield", "prices"],
                            index=["yield", "prices"].index(form.liquid_source), key=f"etf_lq_src_{v}",
                            horizontal=True, disabled=safe != eb.LIQUID,
                            format_func=lambda x: "Fixed yearly yield" if x == "yield" else "Stored prices")
liquid_yield = lq[1].number_input("Yield (% a year)", 0.0, 15.0, float(form.liquid_yield_pct), 0.25,
                                  key=f"etf_lq_yield_{v}", disabled=safe != eb.LIQUID or liquid_source != "yield")
lq[2].caption("LIQUIDBEES pays its interest as daily extra units, which Yahoo's prices only partly show "
              "(they stay flat at Rs 1,000 since Dec-2025). 5% is close to its long-run return after fees.")
ma_days = lq[3].number_input("Also above N-day average (days, 0 = off)", 0, 400, int(form.ma_days), 50, key=f"etf_ma_{v}")
d = st.columns(3)
start = d[0].date_input("Start date", inside(form.start), min_value=first_day, max_value=data.last_day,
                        key=f"etf_start_{v}")
end = d[1].date_input("End date", inside(form.end or data.last_day), min_value=first_day, max_value=data.last_day,
                      key=f"etf_end_{v}")
capital = d[2].number_input("Capital (Rs)", 10_000.0, 1e10, float(form.capital), 100_000.0, key=f"etf_cap_{v}")

with st.expander("💸 Costs and capital gains tax (taken out at every rebalance)", expanded=False):
    k = st.columns(4)
    brokerage = k[0].number_input("Brokerage per order (Rs)", 0.0, 100.0, float(form.costs.delivery_brokerage), 1.0,
                                  key=f"etf_brok_{v}", help="Delivery orders: Rs 0 at Dhan")
    taxes_on = k[1].checkbox("STT, exchange, SEBI, stamp duty, GST", form.costs.include_taxes, key=f"etf_taxes_{v}",
                             help="STT on ETFs: 0.001% on SELL for equity ETFs, none for gold / silver / "
                                  "international / liquid ETFs")
    slippage = k[2].number_input("Slippage (% of price)", 0.0, 2.0, float(form.costs.slippage_value), 0.01,
                                 key=f"etf_slip_{v}", help="Buying a little above and selling a little below the "
                                                           "close (spread and ETF premium / discount)")
    dp = k[3].number_input("DP charge per sell (Rs, + GST)", 0.0, 100.0, float(form.costs.dp_charge), 0.5,
                           key=f"etf_dp_{v}")
    tax = form.tax
    gains_on = st.checkbox("Capital gains tax", tax.enabled, key=f"etf_cg_{v}")
    t = st.columns(6)
    stcg = t[0].number_input("Equity short-term %", 0.0, 50.0, float(tax.equity_stcg_pct), 0.5, key=f"etf_stcg_{v}",
                             disabled=not gains_on)
    ltcg = t[1].number_input("Equity long-term %", 0.0, 50.0, float(tax.equity_ltcg_pct), 0.5, key=f"etf_ltcg_{v}",
                             disabled=not gains_on)
    exempt = t[2].number_input("Equity LTCG free (Rs/yr)", 0.0, 1e7, float(tax.ltcg_exemption), 5_000.0,
                               key=f"etf_exempt_{v}", disabled=not gains_on)
    other = t[3].number_input("Gold/intl long-term %", 0.0, 50.0, float(tax.other_ltcg_pct), 0.5, key=f"etf_other_{v}",
                              disabled=not gains_on)
    slab = t[4].number_input("Your slab %", 0.0, 50.0, float(tax.slab_pct), 1.0, key=f"etf_slab_{v}",
                             disabled=not gains_on,
                             help="Short-term gold / silver / international gains and all LIQUIDBEES gains")
    cess = t[5].number_input("Cess %", 0.0, 10.0, float(tax.cess_pct), 1.0, key=f"etf_cess_{v}", disabled=not gains_on)
    st.caption("Today's rules are used for the whole period: long-term = held more than 12 months; losses "
               "are set off (short-term against any gain, long-term only against long-term) and carried "
               "forward; tax due for the financial year so far is paid at every rebalance (a later loss "
               "in the same year gives some back). Surcharge and the 8-year limit on carried losses are "
               "not modelled.")

problems = []
try:
    settings = eb.EtfBacktestSettings(
        symbols=symbols, top_k=top_k, lookbacks=sorted(lookbacks), weights=weights, method=method,
        skip_recent=int(skip), ma_days=int(ma_days), rebalance=rebalance, abs_filter=abs_filter,
        abs_threshold_pct=threshold, safe_asset=safe, liquid_source=liquid_source, liquid_yield_pct=liquid_yield,
        drift_band_pct=band, start=start, end=end, capital=capital,
        costs=CostSettings(delivery_brokerage=brokerage, slippage_value=slippage, include_taxes=taxes_on, dp_charge=dp),
        tax=eb.TaxSettings(enabled=gains_on, equity_stcg_pct=stcg, equity_ltcg_pct=ltcg, ltcg_exemption=exempt,
                           other_ltcg_pct=other, slab_pct=slab, cess_pct=cess))
    settings.check()
except ValueError as err:
    settings = None
    problems.append(str(err))
for message in problems:
    st.error(message)

bt_tab, claim_tab, sweep_tab, now_tab = st.tabs(["📈 Backtest", "🔍 Check a claim", "🧪 Parameter sweep",
                                                 "📌 Current signal"])

# ---------------------------------------------------------------- backtest
with bt_tab:
    if settings and st.button("▶️ Run backtest", type="primary", key="etf_run"):
        with st.spinner("Running ..."):
            try:
                st.session_state["etf_result"] = eb.run_etf_backtest(data, settings)
                st.session_state["etf_settings"] = settings
            except ValueError as err:
                st.error(str(err))
    result = st.session_state.get("etf_result")
    if result is not None:
        m, x = result.metrics, result.extras
        if result.is_demo:
            st.warning("DEMO DATA: these results come from made-up prices.")
        st.caption(f"{result.start:%d-%b-%Y} to {result.end:%d-%b-%Y} · {x['orders']} orders · everything is "
                   "after brokerage, taxes, slippage and capital gains tax already paid")
        late_start_note(result.start, x["first_etf_day"])
        comp = eb.comparison_table(result).set_index("Measure")
        bench = comp.columns[1] if len(comp.columns) > 1 else None
        r = st.columns(5)
        r[0].metric("Final value", rs(x["final_value"]), pct(m["total_return_pct"]))
        r[1].metric("CAGR", pct(m["cagr_pct"]),
                    f"{m['cagr_pct'] - comp.loc['CAGR %', bench]:+.2f} pts vs Nifty 50" if bench else None)
        r[2].metric("Max drawdown", pct(m["max_drawdown_pct"]), delta_color="off",
                    delta=f"Nifty 50 {pct(comp.loc['Max drawdown %', bench])}" if bench else None)
        r[3].metric("Sharpe", "-" if m["sharpe"] is None else f"{m['sharpe']:.2f}")
        r[4].metric("If all sold at the end", rs(x["liquidation"]["value"]),
                    help="After the exit costs and the capital gains tax on selling everything on the last day")
        r = st.columns(4)
        r[0].metric("Brokerage, taxes and DP", rs(x["charges"]))
        r[1].metric("Slippage", rs(x["slippage"]))
        r[2].metric("Capital gains tax paid", rs(x["tax_paid"]))
        r[3].metric("Tax still due if sold", rs(x["liquidation"]["exit_tax"]))

        st.markdown("**Strategy vs Nifty 50** (Nifty 50 = NIFTYBEES with dividends, bought once, no costs)")
        st.dataframe(comp.reset_index(), hide_index=True, width="stretch")
        st.plotly_chart(charts.equity_chart(result), width="stretch", key="etf_eq")
        st.plotly_chart(charts.drawdown_chart(result), width="stretch", key="etf_dd")

        years = eb.yearly_table(result)
        fig = go.Figure()
        fig.add_bar(x=years["Year"], y=years["Strategy %"], name="Strategy", marker_color=charts.STRATEGY_COLOR)
        if len(years.columns) > 2:
            fig.add_bar(x=years["Year"], y=years.iloc[:, 2], name="Nifty 50", marker_color=charts.BENCHMARK_COLOR)
        fig.update_layout(barmode="group", height=330, margin=dict(l=10, r=10, t=40, b=10), title="Yearly returns",
                          yaxis_ticksuffix="%", xaxis_title="Year", yaxis_title="Return (%)",
                          legend=dict(orientation="h", y=1.1))
        st.plotly_chart(fig, width="stretch", key="etf_years")
        st.dataframe(years, hide_index=True, width="stretch")
        st.caption("The first and last years are part-years (from the start date / to the last stored day).")

        h1, h2 = st.tabs(["Holdings now and rebalances", "Tax by year and trades"])
        with h1:
            st.markdown(f"**Holdings on {result.end:%d-%b-%Y}**")
            st.dataframe(x["holdings"], hide_index=True, width="stretch")
            st.markdown("**Every rebalance** (signal = ranking day, trade = next day's close)")
            st.dataframe(x["rebalances"], hide_index=True, width="stretch")
        with h2:
            st.dataframe(x["tax_by_year"], hide_index=True, width="stretch")
            st.markdown(f"**Sold lots** ({len(result.trades)})")
            st.dataframe(result.trades_frame().drop(columns=["leg_no"]), hide_index=True, width="stretch")
        try:
            st.download_button("⬇️ Excel report", export_excel(result), f"{file_stem(result)}.xlsx", key="etf_xlsx",
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        except ValueError as err:
            st.warning(str(err))

        st.markdown("**Save to Strategy Library**")
        s1, s2 = st.columns([3, 1])
        name = s1.text_input("Name", f"ETF momentum top {result.params['top_k']} {result.params['rebalance']}",
                             key="etf_save_name", label_visibility="collapsed")
        if s2.button("💾 Save to Strategy Library", key="etf_save"):
            try:
                sid = eb.save_etf_strategy(name, st.session_state["etf_settings"])
                store.save_run(sid, {}, result)              # shown as its last backtest in module 10
                st.success(f"Saved '{name.strip()}' (strategy id {sid}).")
            except Exception as err:
                st.error(f"Could not save: {err}")
        st.caption(FUTURE_NOTE)
    elif settings:
        st.caption("Pick the settings above and press Run.")

# ---------------------------------------------------------------- check a claim
with claim_tab:
    st.markdown("Someone says a strategy like this made a big return. Type the claim and its period: the page runs "
                "**the settings above** over that period without costs, with costs and with tax, next to Nifty 50.")
    q = st.columns(3)
    claimed = q[0].number_input("Claimed total return (%)", -100.0, 100_000.0, 670.0, 10.0, key="etf_claim")
    c_start = q[1].date_input("From", inside(dt.date(2016, 11, 1)), min_value=first_day,
                              max_value=data.last_day, key="etf_claim_start")
    c_end = q[2].date_input("To", data.last_day, min_value=first_day, max_value=data.last_day,
                            key="etf_claim_end")
    if settings and st.button("🔍 Check the claim", key="etf_claim_run"):
        with st.spinner("Running three backtests ..."):
            try:
                st.session_state["etf_claim_out"] = eb.check_claim(data, settings, claimed, c_start, c_end)
            except ValueError as err:
                st.error(str(err))
    out = st.session_state.get("etf_claim_out")
    if out is not None:
        table, steps = out["table"], out["steps"]
        first, last = steps["period"]
        st.caption(f"My backtest ran {first:%d-%b-%Y} to {last:%d-%b-%Y} (the first day money could be invested "
                   "to the last stored day).")
        late_start_note(first, steps["first_etf_day"])
        st.dataframe(table, hide_index=True, width="stretch",
                     column_config={"Final value of Rs 1 lakh": st.column_config.NumberColumn(format="Rs %.0f")})
        colors = [charts.BENCHMARK_COLOR if "Nifty" in line else "#9a9a9a" if line == "Claim" else charts.STRATEGY_COLOR
                  for line in table["Line"]]
        fig = go.Figure(go.Bar(x=table["Total return %"], y=table["Line"], orientation="h", marker_color=colors,
                               text=table["Total return %"].map(lambda v: f"{v:,.1f}%"), textposition="auto"))
        fig.update_layout(height=320, margin=dict(l=10, r=10, t=30, b=10), xaxis_ticksuffix="%",
                          xaxis_title="Total return (%)",
                          yaxis=dict(autorange="reversed"), title="Total return over the period")
        st.plotly_chart(fig, width="stretch", key="etf_claim_chart")
        st.markdown(
            f"- **Costs** (brokerage, taxes, DP, slippage) took **{steps['costs_points']:.1f} percentage points** "
            f"of total return ({rs(steps['charges_rs'])} charges + {rs(steps['slippage_rs'])} slippage).\n"
            f"- **Capital gains tax paid at rebalances** took **{steps['tax_points']:.1f} points** "
            f"({rs(steps['tax_rs'])}).\n"
            + (f"- **Selling everything at the end** (exit costs and the tax still due) takes "
               f"**{steps['exit_points']:.1f} points** more." if steps["exit_points"] >= 0 else
               f"- **Selling everything at the end** adds **{-steps['exit_points']:.1f} points**: the open "
               "positions are at a loss, which gives back part of the tax paid earlier this financial year."))
        st.caption("A claim is often before costs and tax, over a hand-picked period, with settings chosen after "
                   "seeing the results. A big gap between the claim and 'before costs' usually means a different "
                   "period, ETF list or rule. " + FUTURE_NOTE)

# ---------------------------------------------------------------- parameter sweep
with sweep_tab:
    st.markdown("The same settings with **top K = 1 to 5** and six lookback sets. If small changes give very "
                "different results, the backtest is fragile: do not trust the single best cell.")
    if settings and st.button("🧪 Run the sweep (30 backtests)", key="etf_sweep_run"):
        bar = st.progress(0.0, "Running ...")
        st.session_state["etf_sweep"] = eb.parameter_sweep(
            data, settings, progress=lambda done, total: bar.progress(done / total, f"{done} of {total}"))
        bar.empty()
    sweep = st.session_state.get("etf_sweep")
    if sweep is not None:
        grid = sweep.pivot(index="Lookbacks", columns="Top K", values="CAGR %")
        fig = go.Figure(go.Heatmap(z=grid.values, x=[f"K={k}" for k in grid.columns], y=list(grid.index),
                                   colorscale="Blues", text=grid.map(lambda v: f"{v:.1f}%").values,
                                   texttemplate="%{text}", colorbar=dict(title="CAGR %"),
                                   hovertemplate="%{y}, %{x}: CAGR %{z:.2f}%<extra></extra>"))
        fig.update_layout(height=360, margin=dict(l=10, r=10, t=40, b=10), title="CAGR % after costs and tax",
                          xaxis_title="Top K (ETFs held)", yaxis_title="Lookbacks")
        st.plotly_chart(fig, width="stretch", key="etf_sweep_chart")
        spread = sweep["CAGR %"].max() - sweep["CAGR %"].min()
        st.markdown(f"CAGR ranges from **{sweep['CAGR %'].min():.1f}%** to **{sweep['CAGR %'].max():.1f}%** "
                    f"(a spread of {spread:.1f} points); the middle (median) result is "
                    f"**{sweep['CAGR %'].median():.1f}%**.")
        st.dataframe(sweep, hide_index=True, width="stretch")

# ---------------------------------------------------------------- current signal
with now_tab:
    if settings:
        table, target = eb.current_signal(data, settings)
        table.attrs = {}                          # extra details the table cannot show
        st.markdown(f"Ranked at the close of **{data.last_day:%d-%b-%Y}** (the last stored day). "
                    f"At the next {settings.rebalance} rebalance the strategy would hold:")
        st.dataframe(pd.DataFrame({"Hold": list(target), "Weight %": [round(w * 100, 1) for w in target.values()]})
                     if target else pd.DataFrame({"Hold": ["Cash"], "Weight %": [100.0]}),
                     hide_index=True)
        st.dataframe(table, hide_index=True, width="stretch", column_config={
            "score": st.column_config.NumberColumn("Score", format="%.4f"),
            "abs_return_pct": st.column_config.NumberColumn("Return over lookbacks %", format="%.2f")})
        st.caption("Update prices first (Data Downloader page or `python scripts/sync_prices.py --universe etfs`). "
                   "This is what the rules say, not advice to buy.")

historical_note()
