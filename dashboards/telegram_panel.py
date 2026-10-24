"""Module 12 - Telegram Bot Control Panel (planned)."""
import streamlit as st

from marketlens.ui import coming_soon, key_notice

st.title("💬 Telegram Bot Control Panel")
key_notice("Telegram", ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"],
           "Alerts will only be shown on this page until then.")
coming_soon("Telegram Bot Control Panel", [
    "Telegram messages for signals, price levels, order fills and daily P&L",
    "Bot commands: /status, /positions, /pnl, /stop (kill switch)",
    "Add, list and delete price alerts",
    "Send a test message and see the bot's message history",
])
