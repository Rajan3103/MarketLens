"""Settings & Status: shows which keys are filled in, without showing the keys."""
import streamlit as st

from marketlens import config

st.title("⚙️ Settings & Status")
st.write("Keys are read from the `.env` file. Copy `.env.example` to `.env` and fill it in.")

checks = {
    "Dhan client ID": config.DHAN_CLIENT_ID,
    "Dhan access token": config.DHAN_ACCESS_TOKEN,
    "Telegram bot token": config.TELEGRAM_BOT_TOKEN,
    "Telegram chat ID": config.TELEGRAM_CHAT_ID,
}
for name, value in checks.items():
    if config.is_set(value):
        st.write(f"✅ {name}: set")
    else:
        st.write(f"⬜ {name}: not set yet")

st.write(f"Trading mode: **{config.TRADING_MODE}**")
