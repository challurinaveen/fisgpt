"""
Dashboard page — key metrics, charts, and data overview at a glance.
"""
from __future__ import annotations

import sys
from pathlib import Path
_FIS_GPT = str(Path(__file__).resolve().parent.parent.parent)
if _FIS_GPT not in sys.path:
    sys.path.insert(0, _FIS_GPT)

import streamlit as st
import pandas as pd

from phase5.shared import get_db_connection, get_db_stats, BRAND


# ── helpers ───────────────────────────────────────────────────────────

@st.cache_data(ttl=300)
def _query(sql: str) -> pd.DataFrame:
    con = get_db_connection()
    return con.execute(sql).fetchdf()


# ── page entry point ──────────────────────────────────────────────────

def render():
    st.markdown("## 📊 FoodFax Dashboard")
    st.caption("A snapshot of 30+ years of product testing data.")

    # ── top-line metrics ──────────────────────────────────────────────
    stats = get_db_stats()
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("🧪 Product Tests", f"{stats['tests']:,}")
    c2.metric("📁 Categories", f"{stats['categories']:,}")
    c3.metric("🏭 Manufacturers", f"{stats['manufacturers']:,}")
    c4.metric("📅 First Year", stats["year_min"])
    c5.metric("📅 Latest Year", stats["year_max"])

    st.divider()

    # ── row 1: Tests per year + Source breakdown ──────────────────────
    col_left, col_right = st.columns(2)

    with col_left:
        st.markdown("##### Tests per year")
        by_year = _query("""
            SELECT test_year AS year, count(*) AS tests
            FROM curated.product_test_v
            WHERE test_year >= 2002
            GROUP BY test_year
            ORDER BY test_year
        """)
        st.bar_chart(by_year.set_index("year"), color=BRAND["primary"])

    with col_right:
        st.markdown("##### Data sources")
        by_source = _query("""
            SELECT
                source_table AS source,
                count(*) AS tests
            FROM curated.product_test_v
            GROUP BY source_table
            ORDER BY tests DESC
        """)
        # Rename for readability
        source_labels = {
            "norm_data":         "Current Norm",
            "historic_products": "Historic Archive",
            "ffx_pre_2021":      "Pre-2021 Update",
            "session_pdf":       "2025 Session PDFs",
        }
        by_source["source"] = by_source["source"].map(
            lambda x: source_labels.get(x, x)
        )

        for _, row in by_source.iterrows():
            pct = row["tests"] / stats["tests"] * 100
            st.markdown(
                f"**{row['source']}** — {row['tests']:,} "
                f"({pct:.0f}%)"
            )
            st.progress(min(pct / 100, 1.0))

    st.divider()

    # ── row 2: Top categories + Brand vs Own Label ────────────────────
    col_left, col_right = st.columns(2)

    with col_left:
        st.markdown("##### Top 15 categories (by test count)")
        top_cats = _query("""
            SELECT category_name AS category, count(*) AS tests
            FROM curated.product_test_v
            GROUP BY category_name
            ORDER BY tests DESC
            LIMIT 15
        """)
        st.bar_chart(
            top_cats.set_index("category"),
            horizontal=True,
            color=BRAND["primary"],
        )

    with col_right:
        st.markdown("##### Brand vs Own Label")
        brand_split = _query("""
            SELECT
                own_label_or_brand AS type,
                count(*) AS tests
            FROM curated.product_test_v
            WHERE own_label_or_brand IN ('Brand', 'Own Label')
            GROUP BY own_label_or_brand
            ORDER BY tests DESC
        """)

        for _, row in brand_split.iterrows():
            pct = row["tests"] / brand_split["tests"].sum() * 100
            st.metric(
                row["type"],
                f"{row['tests']:,}",
                f"{pct:.0f}%",
            )

        st.markdown("")
        st.markdown("##### Tier breakdown")
        tier_split = _query("""
            SELECT tier, count(*) AS tests
            FROM curated.product_test_v
            WHERE tier IN ('Premium', 'Standard', 'Value')
            GROUP BY tier
            ORDER BY tests DESC
        """)
        for _, row in tier_split.iterrows():
            st.markdown(f"**{row['tier']}** — {row['tests']:,}")

    st.divider()

    # ── row 3: 2025 Session overview ──────────────────────────────────
    st.markdown("##### 2025 Session Sets — Performance overview")

    session_df = _query("""
        SELECT
            session_set,
            count(*) AS products,
            round(avg(score_out_of_50), 1) AS avg_score,
            sum(CASE WHEN vs_category_norm > 0 THEN 1 ELSE 0 END) AS beat_norm,
            sum(CASE WHEN award_quality THEN 1 ELSE 0 END) AS quality_awards,
            sum(CASE WHEN award_taste THEN 1 ELSE 0 END) AS taste_awards,
            sum(CASE WHEN award_value THEN 1 ELSE 0 END) AS value_awards
        FROM curated.session_report_v
        GROUP BY session_set
        ORDER BY session_set
    """)

    st.dataframe(
        session_df.rename(columns={
            "session_set": "Set",
            "products": "Products",
            "avg_score": "Avg Score",
            "beat_norm": "Beat Norm",
            "quality_awards": "Quality ✅",
            "taste_awards": "Taste ✅",
            "value_awards": "Value ✅",
        }),
        use_container_width=True,
        hide_index=True,
    )

    st.divider()

    # ── row 4: Project types + Storage ────────────────────────────────
    col_left, col_right = st.columns(2)

    with col_left:
        st.markdown("##### Project types")
        projects = _query("""
            SELECT project_type AS type, count(*) AS tests
            FROM curated.product_test_v
            WHERE project_type IS NOT NULL
            GROUP BY project_type
            ORDER BY tests DESC
        """)
        st.bar_chart(projects.set_index("type"), color=BRAND["secondary"])

    with col_right:
        st.markdown("##### Storage types (2025)")
        storage = _query("""
            SELECT
                COALESCE(storage, 'Unknown') AS storage,
                count(*) AS tests,
                round(avg(price_gbp), 2) AS avg_price
            FROM curated.product_test_v
            WHERE test_year = 2025
            GROUP BY storage
            ORDER BY tests DESC
        """)
        for _, row in storage.iterrows():
            import math
            avg_p = row["avg_price"]
            price_str = ""
            if avg_p is not None and not (isinstance(avg_p, float) and math.isnan(avg_p)):
                price_str = f" · avg £{avg_p:.2f}"
            st.markdown(f"**{row['storage']}** — {row['tests']:,} tests{price_str}")

    st.divider()
    st.caption(
        "All data from the FoodFax DuckDB warehouse. "
        "Charts update automatically when new data is loaded."
    )
