"""
Simple password authentication for the F!S Internal GPT.

Set the password in .env:
    APP_PASSWORD=your-team-password

If APP_PASSWORD is not set, authentication is disabled (open access).
"""
from __future__ import annotations

import os
import hashlib
import streamlit as st


def check_password() -> bool:
    """
    Returns True if no password is configured or the user has authenticated.
    Call this at the top of app.py — it blocks rendering until auth passes.
    """
    password = os.environ.get("APP_PASSWORD", "").strip()
    if not password:
        return True  # No password configured — open access

    # Already authenticated this session
    if st.session_state.get("authenticated"):
        return True

    # Show login form
    st.markdown(
        '<h2 style="text-align:center;color:#E8553A">'
        '🍽️ F!S Internal GPT</h2>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<p style="text-align:center;color:#6C757D">'
        'Internal tool — please sign in</p>',
        unsafe_allow_html=True,
    )

    with st.form("login_form"):
        entered = st.text_input(
            "Password",
            type="password",
            placeholder="Enter team password",
        )
        submitted = st.form_submit_button(
            "Sign in",
            use_container_width=True,
        )

        if submitted:
            if entered == password:
                st.session_state.authenticated = True
                st.rerun()
            else:
                st.error("Incorrect password. Please try again.")

    return False
