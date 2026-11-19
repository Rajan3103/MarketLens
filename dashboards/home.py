"""Home page: what MarketLens is and how to use the menu (the text lives in marketlens/home.py)."""
import streamlit as st

from marketlens import home
from marketlens.ui import help_note

st.title(f"📈 {home.TITLE}")
st.subheader(home.SUBTITLE)
help_note(home.HELP)

rows = "\n".join(f"| {f'**{group}**' if group else ''} | {num} | {name} | {what} | ✅ Ready |"
                 for group, num, name, what in home.MODULES)
st.markdown("Pick a dashboard from the menu on the left.\n\n"
            "| Group | # | Module | What it does | Status |\n| --- | --- | --- | --- | --- |\n" + rows)

st.info(home.TIP.replace("Settings & Status", "**Settings & Status**"))
st.caption(home.NOTE)
