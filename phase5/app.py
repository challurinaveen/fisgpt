"""
Phase 5 · Streamlit web UI for the F!S Internal GPT.

Multi-page app with Chat, Data Explorer, and Dashboard.

Run with:
    streamlit run fis-gpt/phase5/app.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Ensure fis-gpt is on the path
_APP_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_APP_DIR.parent))

# Load .env
from dotenv import load_dotenv
load_dotenv(_APP_DIR.parent / ".env")

import streamlit as st

# ── page config (must be first Streamlit call) ───────────────────────
st.set_page_config(
    page_title="F!S Internal GPT",
    page_icon="🍽️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── authentication (optional — set APP_PASSWORD in .env) ─────────────
from phase5.auth import check_password
if not check_password():
    st.stop()

# ── pages ─────────────────────────────────────────────────────────────
from phase5.pages import chat, explorer, dashboard

pg = st.navigation([
    st.Page(chat.render,      title="Chat",          icon="💬", default=True, url_path="chat"),
    st.Page(explorer.render,  title="Data Explorer",  icon="🔍", url_path="explorer"),
    st.Page(dashboard.render, title="Dashboard",      icon="📊", url_path="dashboard"),
])

# ── shared sidebar ────────────────────────────────────────────────────
from phase5.shared import render_sidebar
render_sidebar()

# ── run selected page ─────────────────────────────────────────────────
pg.run()
