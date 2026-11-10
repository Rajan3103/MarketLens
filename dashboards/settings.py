"""Settings & Status: shows which keys are filled in, without showing the keys.

Requirements FR-32 and NFR-07.
"""
import pandas as pd
import streamlit as st

from marketlens import config
from marketlens.db import connection_status
from marketlens.ui import trading_mode_banner

st.title("⚙️ Settings & Status")
st.info(
    "🔒 Keys are read from the `.env` file on this computer. "
    "`.env` is listed in `.gitignore`, so it is **never uploaded** to GitHub. "
    "Copy `.env.example` to `.env` to start, and restart the app after editing it."
)


# Check the database, remembering the answer for 30 seconds so the page stays fast.
@st.cache_data(ttl=30, show_spinner="Checking the database ...")
def cached_db_status() -> tuple[bool, str]:
    return connection_status()


if not config.is_set(config.DASHBOARD_PASSWORD):
    st.warning("🔓 DASHBOARD_PASSWORD is empty: anyone who can open this app can see your data and press its "
               "buttons (kill switch, deploy, square off). Set DASHBOARD_PASSWORD in .env and restart the app, "
               "especially before running it on a server.")

# ---- 1. Trading mode ----
st.subheader("Trading mode")
trading_mode_banner()
if config.AUTO_TRADE:
    st.warning("AUTO_TRADE is on: strategies may place orders without asking you.")

# ---- 2. Database (PostgreSQL) ----
st.subheader("Database")
ok, message = cached_db_status()
if ok:
    st.success(f"✅ {message}")
else:
    st.error(f"❌ {message}")
    st.caption("First time? Run `python scripts/init_db.py` in the terminal to create the database.")
if st.button("Check again"):
    cached_db_status.clear()
    st.rerun()

# ---- 3. Keys: set or not (the values themselves are never shown) ----
st.subheader("API keys")
status = config.key_status()
rows = [
    {"Key": name, "Status": "✅ set" if done else "❌ not set", "What it unlocks": unlocks}
    for name, done, unlocks in status
]
st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
missing = sum(1 for _, done, _ in status if not done)
if missing:
    st.caption(f"{missing} key(s) not set. Pages that need them show DEMO data or a 'key not set' message. "
               "DHAN_API_KEY / DHAN_API_SECRET are optional: a token pasted from web.dhan.co is enough.")
if config.is_set(config.WEBHOOK_SECRET) and not config.webhook_secret_strong():
    st.warning(f"WEBHOOK_SECRET is shorter than {config.WEBHOOK_SECRET_MIN_LENGTH} characters, "
               "so it is easy to guess. Make a new one: "
               "`python -c \"import secrets; print(secrets.token_urlsafe(32))\"`")

# ---- 4. Other settings ----
st.subheader("Other settings")
others = {
    "TRADING_MODE": config.TRADING_MODE,
    "PAPER_CAPITAL": config.PAPER_CAPITAL,
    "AUTO_TRADE": config.AUTO_TRADE,
    "MAX_DAILY_LOSS_RS": f"Rs {config.MAX_DAILY_LOSS_RS:,.0f}",
    "MAX_ORDERS_PER_SECOND": config.MAX_ORDERS_PER_SECOND,
    "MAX_OPEN_POSITIONS": config.MAX_OPEN_POSITIONS,
    "DEMO_MODE": config.DEMO_MODE,
    "DATA_SOURCE": config.DATA_SOURCE,
    "DOWNLOAD_DIR": config.DOWNLOAD_DIR,
    "API_PORT": config.API_PORT,
    "DATABASE_URL": config.safe_database_url(),  # password hidden
    "TEST_DATABASE_URL": config.safe_database_url(config.TEST_DATABASE_URL) or "(not set)",
    "AWS_REGION": config.AWS_REGION,
    "SERVER_STATIC_IP": config.SERVER_STATIC_IP or "(not set - needed for live orders on AWS)",
}
st.dataframe(pd.DataFrame({"Setting": others.keys(), "Value": [str(v) for v in others.values()]}),
             hide_index=True, width="stretch")

# ---- 5. Scheduled jobs (python -m marketlens.scheduler) ----
st.subheader("Scheduled jobs")
if not ok:
    st.caption("Needs the database: the scheduler saves every run there.")
else:
    from marketlens.scheduler import jobs as sched
    from marketlens.trading import live_runner as lr
    from marketlens.trading import monitor as mon
    state, text = mon.heartbeat_state(lr.heartbeats().get(sched.HEARTBEAT))
    if state == "alive":
        st.success(f"🟢 Scheduler running: {text}")
    else:
        st.warning(("⚪ Scheduler not started. " if state == "never" else f"🔴 Scheduler stopped ({text}). ")
                   + "Start it in its own terminal: `python -m marketlens.scheduler`")
    r1, r2 = st.columns([3, 1])
    pick = r1.selectbox("Run a job now", [j.id for j in sched.JOBS], format_func=lambda i: sched.BY_ID[i].name,
                        key="sched_pick")
    if r2.button("▶️ Run now", key="sched_run", width="stretch"):
        with st.spinner(f"Running {sched.BY_ID[pick].name} ..."):
            status, result = sched.run_job(pick, force=True)
        (st.success if status == "ok" else st.info if status == "skipped" else st.error)(f"{status}: {result}")
    # the table comes after the button, so it already shows a run started just now
    icons = {"ok": "✅ ok", "failed": "❌ failed", "skipped": "⏭️ skipped", "running": "⏳ running",
             "never run": "never run"}
    table = pd.DataFrame(sched.job_table())
    table["status"] = table["status"].map(icons).fillna(table["status"])
    st.dataframe(table[["job", "when", "next_run", "last_run", "status", "result"]], hide_index=True, width="stretch",
                 column_config={"next_run": st.column_config.DatetimeColumn("Next run", format="ddd DD-MMM HH:mm"),
                                "last_run": st.column_config.DatetimeColumn("Last run", format="DD-MMM HH:mm")})
    holidays = sched.load_holidays()
    coming = sorted(d for d in holidays if d >= pd.Timestamp.now(tz="Asia/Kolkata").date())[:3]
    st.caption(f"Market jobs skip weekends and the {len(holidays)} NSE holidays in data/nse_holidays.csv"
               + (" (next: " + ", ".join(f"{d:%d-%b} {holidays[d]}" for d in coming) + ")" if coming else "")
               + ". Add next year's list from NSE's holiday circular each December.")
