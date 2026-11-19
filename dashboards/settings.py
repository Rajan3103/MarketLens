"""Settings & Status: shows which keys are filled in, without showing the keys.

Requirements FR-32 and NFR-07. What is shown is worked out in marketlens/settings_info.py (shared with the web
API's /api/settings and /api/scheduler routes); this page only draws it.
"""
import pandas as pd
import streamlit as st

from marketlens import settings_info as info
from marketlens.market_clock import holiday_note
from marketlens.ui import trading_mode_banner

st.title("⚙️ Settings & Status")
st.info("🔒 " + info.INTRO.replace(".env", "`.env`").replace("`.env`.example", "`.env.example`")
        .replace(".gitignore", "`.gitignore`").replace("never uploaded", "**never uploaded**"))


# Check the database, remembering the answer for 30 seconds so the page stays fast.
@st.cache_data(ttl=30, show_spinner="Checking the database ...")
def cached_db_status() -> dict:
    return info.database_status()


messages = info.page_messages()
for m in messages:                                   # the password warning comes first, as before
    if m["text"].startswith("DASHBOARD_PASSWORD"):
        st.warning("🔓 " + m["text"])

# ---- 1. Trading mode ----
st.subheader("Trading mode")
trading_mode_banner()
for m in messages:
    if m["text"].startswith("AUTO_TRADE"):
        st.warning(m["text"])

# ---- 2. Database (PostgreSQL) ----
st.subheader("Database")
db = cached_db_status()
ok = db["ok"]
if ok:
    st.success(f"✅ {db['message']}")
else:
    st.error(f"❌ {db['message']}")
    st.caption(db["hint"])
if st.button("Check again"):
    cached_db_status.clear()
    st.rerun()

# ---- 3. Keys: set or not (the values themselves are never shown) ----
st.subheader("API keys")
rows = [{"Key": k["name"], "Status": "✅ set" if k["set"] else "❌ not set", "What it unlocks": k["unlocks"]}
        for k in info.key_rows()]
st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
for m in messages:
    if m["text"].startswith("WEBHOOK_SECRET"):
        st.warning(m["text"])
    elif m["level"] == "info":
        st.caption(m["text"])

# ---- 4. Other settings ----
st.subheader("Other settings")
others = info.other_settings()
st.dataframe(pd.DataFrame({"Setting": [o["name"] for o in others], "Value": [o["value"] for o in others]}),
             hide_index=True, width="stretch")

# ---- 5. Scheduled jobs (python -m marketlens.scheduler) ----
st.subheader("Scheduled jobs")
if not ok:
    st.caption(info.NEEDS_DB)
else:
    from marketlens.scheduler import jobs as sched
    status = info.scheduler_status()
    if status["state"] == "alive":
        st.success(f"🟢 {status['text']}")
    else:
        st.warning(("⚪ " if status["state"] == "never" else "🔴 ") + status["text"].replace(
            info.SCHEDULER_START, f"`{info.SCHEDULER_START}`"))
    r1, r2 = st.columns([3, 1])
    pick = r1.selectbox("Run a job now", [j.id for j in sched.JOBS], format_func=lambda i: sched.BY_ID[i].name,
                        key="sched_pick")
    if r2.button("▶️ Run now", key="sched_run", width="stretch"):
        with st.spinner(f"Running {sched.BY_ID[pick].name} ..."):
            run_status, result = sched.run_job(pick, force=True)
        (st.success if run_status == "ok" else st.info if run_status == "skipped" else st.error)(
            f"{run_status}: {result}")
    # the table comes after the button, so it already shows a run started just now
    icons = {"ok": "✅ ok", "failed": "❌ failed", "skipped": "⏭️ skipped", "running": "⏳ running",
             "never run": "never run"}
    job_list, _ = info.job_rows()
    table = pd.DataFrame(job_list)
    table["status"] = table["status"].map(icons).fillna(table["status"])
    st.dataframe(table[["job", "when", "next_run", "last_run", "status", "result"]], hide_index=True, width="stretch",
                 column_config={"next_run": st.column_config.DatetimeColumn("Next run", format="ddd DD-MMM HH:mm"),
                                "last_run": st.column_config.DatetimeColumn("Last run", format="DD-MMM HH:mm")})
    holidays = sched.load_holidays()
    st.caption(holiday_note(pd.Timestamp.now(tz="Asia/Kolkata").date(), holidays))
