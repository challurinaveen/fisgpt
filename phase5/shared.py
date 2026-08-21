"""
Shared components: sidebar, branding, database helpers.
"""
from __future__ import annotations
import os
import streamlit as st


# ── F!S brand colours ─────────────────────────────────────────────────
BRAND = {
    "primary":    "#E8553A",   # F!S coral/red
    "secondary":  "#2C3E50",   # dark navy
    "accent":     "#27AE60",   # green for positive
    "warning":    "#F39C12",   # amber
    "bg_light":   "#F8F9FA",
    "text_muted": "#6C757D",
}


# ── custom CSS ────────────────────────────────────────────────────────
def inject_css():
    st.markdown(f"""
    <style>
        /* Main container */
        .main .block-container {{
            padding-top: 1.5rem;
        }}

        /* Chat message styling */
        .stChatMessage {{
            border-radius: 12px;
        }}

        /* Sidebar header */
        .sidebar-header {{
            font-size: 0.8rem;
            color: {BRAND['text_muted']};
            text-transform: uppercase;
            letter-spacing: 0.05em;
            margin-bottom: 0.5rem;
            font-weight: 600;
        }}

        /* Hide the default Streamlit footer */
        footer {{ display: none; }}

        /* Brand accent on buttons */
        .stButton > button:hover {{
            border-color: {BRAND['primary']};
            color: {BRAND['primary']};
        }}

        /* Metric cards */
        [data-testid="stMetric"] {{
            background: {BRAND['bg_light']};
            border-radius: 8px;
            padding: 0.75rem;
        }}

        /* Tab styling */
        .stTabs [data-baseweb="tab-list"] {{
            gap: 0.5rem;
        }}
        .stTabs [data-baseweb="tab"] {{
            border-radius: 8px 8px 0 0;
        }}
    </style>
    """, unsafe_allow_html=True)


# ── database connection ───────────────────────────────────────────────

@st.cache_resource
def get_db_connection():
    """Get a cached read-only DuckDB connection."""
    from phase2 import warehouse
    return warehouse.connect(read_only=True)


@st.cache_data(ttl=300)
def get_db_stats():
    """Get high-level database statistics (cached 5 min)."""
    con = get_db_connection()
    stats = con.execute("""
        SELECT
            count(*) AS tests,
            count(DISTINCT category_name) AS cats,
            count(DISTINCT manufacturer_name) AS mfrs,
            min(test_year) AS yr_min,
            max(test_year) AS yr_max
        FROM curated.product_test_v
    """).fetchone()
    return {
        "tests": stats[0],
        "categories": stats[1],
        "manufacturers": stats[2],
        "year_min": stats[3],
        "year_max": stats[4],
    }


# ── provider detection ────────────────────────────────────────────────

def detect_default_provider() -> str:
    """Pick the first provider that has an API key configured."""
    if os.environ.get("OPENAI_API_KEY"):
        return "gpt-4o-mini"
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "sonnet"
    if os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY"):
        return "gemini-flash"
    return "gpt-4o-mini"


def get_available_models() -> list[tuple[str, str]]:
    """Return (label, key) pairs for models with configured API keys."""
    models = []
    if os.environ.get("OPENAI_API_KEY"):
        models.extend([
            ("GPT-4o Mini", "gpt-4o-mini"),
            ("GPT-4o", "gpt-4o"),
        ])
    if os.environ.get("ANTHROPIC_API_KEY"):
        models.extend([
            ("Claude Sonnet", "sonnet"),
            ("Claude Haiku", "haiku"),
        ])
    if os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY"):
        models.extend([
            ("Gemini Flash", "gemini-flash"),
            ("Gemini Pro", "gemini-pro"),
        ])
    if not models:
        models = [("GPT-4o Mini (no key)", "gpt-4o-mini")]
    return models


# ── session state init ────────────────────────────────────────────────

def init_state():
    """Set up session state defaults (idempotent)."""
    defaults = {
        "messages": [],
        "provider_key": "gpt-4o-mini",
        "verbose": False,
        "total_tokens": 0,
        "total_queries": 0,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


# ── sidebar ───────────────────────────────────────────────────────────

def render_sidebar():
    """Render the shared sidebar across all pages."""
    inject_css()
    init_state()

    with st.sidebar:
        # Branding — logo + title
        import base64
        from pathlib import Path
        logo_path = Path(__file__).resolve().parent.parent / "f_s_group_logo.jpg"
        if logo_path.exists():
            logo_b64 = base64.b64encode(logo_path.read_bytes()).decode()
            st.markdown(
                f'<div style="text-align:center;margin-bottom:0.5rem">'
                f'<img src="data:image/jpeg;base64,{logo_b64}" '
                f'style="width:160px;border-radius:8px" /></div>',
                unsafe_allow_html=True,
            )
        st.markdown(
            '<h3 style="text-align:center;margin:0">Internal GPT</h3>',
            unsafe_allow_html=True,
        )
        st.caption("Food Insight & Strategy")
        st.divider()

        # Database stats
        st.markdown('<p class="sidebar-header">Database</p>', unsafe_allow_html=True)
        try:
            stats = get_db_stats()
            c1, c2 = st.columns(2)
            c1.metric("Tests", f"{stats['tests']:,}")
            c2.metric("Categories", f"{stats['categories']:,}")
            st.caption(f"📅 {stats['year_min']} – {stats['year_max']}  ·  "
                       f"🏭 {stats['manufacturers']:,} manufacturers")
        except Exception as e:
            st.caption(f"⚠️ {e}")

        st.divider()

        # Session stats
        st.markdown('<p class="sidebar-header">Session</p>', unsafe_allow_html=True)
        c1, c2 = st.columns(2)
        c1.metric("Queries", st.session_state.total_queries)
        c2.metric("Tokens", f"{st.session_state.total_tokens:,}")

        st.divider()
        st.toggle("Show tool calls", key="verbose")

        if st.button("🗑️ Clear chat", use_container_width=True):
            st.session_state.messages = []
            st.session_state.total_tokens = 0
            st.session_state.total_queries = 0
            st.rerun()

        st.divider()
        st.caption("Built by F!S Group · Powered by FoodFax")
