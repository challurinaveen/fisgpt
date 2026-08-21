"""
Data Explorer page — browse products, categories, and measures
without writing queries.
"""
from __future__ import annotations

import sys
from pathlib import Path
_FIS_GPT = str(Path(__file__).resolve().parent.parent.parent)
if _FIS_GPT not in sys.path:
    sys.path.insert(0, _FIS_GPT)

import streamlit as st
import pandas as pd

from phase5.shared import get_db_connection


# ── helpers ───────────────────────────────────────────────────────────

@st.cache_data(ttl=300)
def _query(sql: str, params: list | None = None) -> pd.DataFrame:
    """Run a read-only query and return a DataFrame."""
    con = get_db_connection()
    if params:
        return con.execute(sql, params).fetchdf()
    return con.execute(sql).fetchdf()


# ── tab: Products ─────────────────────────────────────────────────────

def _tab_products():
    st.markdown("#### 🛒 Product Browser")
    st.caption("Search and filter across 25,000+ product tests.")

    # Filters
    col1, col2, col3, col4 = st.columns(4)

    with col1:
        search = st.text_input("🔍 Search product name", placeholder="e.g. Walkers")
    with col2:
        years = _query("SELECT DISTINCT test_year FROM curated.product_test_v ORDER BY test_year DESC")
        year_options = ["All"] + years["test_year"].tolist()
        year_filter = st.selectbox("Year", year_options)
    with col3:
        brand_filter = st.selectbox("Type", ["All", "Brand", "Own Label"])
    with col4:
        source_filter = st.selectbox("Source", ["All", "session_pdf", "norm_data",
                                                 "historic_products", "ffx_pre_2021"])

    # Build query
    conditions = []
    params = []

    if search:
        conditions.append("product_name ILIKE ?")
        params.append(f"%{search}%")
    if year_filter != "All":
        conditions.append("test_year = ?")
        params.append(int(year_filter))
    if brand_filter != "All":
        conditions.append("own_label_or_brand = ?")
        params.append(brand_filter)
    if source_filter != "All":
        conditions.append("source_table = ?")
        params.append(source_filter)

    where = "WHERE " + " AND ".join(conditions) if conditions else ""

    count = _query(f"SELECT count(*) AS n FROM curated.product_test_v {where}", params)
    total = count["n"].iloc[0]

    st.markdown(f"**{total:,}** products match your filters")

    if total > 0:
        df = _query(f"""
            SELECT
                product_name,
                manufacturer_name,
                category_name,
                test_year,
                session_set,
                own_label_or_brand AS type,
                tier,
                price_gbp AS price,
                source_table AS source
            FROM curated.product_test_v
            {where}
            ORDER BY test_year DESC, product_name
            LIMIT 200
        """, params)

        st.dataframe(
            df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "price": st.column_config.NumberColumn("Price (£)", format="£%.2f"),
                "test_year": st.column_config.NumberColumn("Year", format="%d"),
            },
        )

        if total > 200:
            st.caption(f"Showing 200 of {total:,} results. Narrow your filters to see more.")


# ── tab: Categories ───────────────────────────────────────────────────

def _tab_categories():
    st.markdown("#### 📁 Category Browser")
    st.caption("See test counts and trends across 460+ food & drink categories.")

    col1, col2 = st.columns([2, 1])
    with col1:
        cat_search = st.text_input("🔍 Search categories", placeholder="e.g. Chocolate")
    with col2:
        sort_by = st.selectbox("Sort by", ["Most tested", "Least tested", "A-Z"])

    order = {
        "Most tested": "n DESC",
        "Least tested": "n ASC",
        "A-Z": "category_name ASC",
    }[sort_by]

    cat_where = ""
    cat_params = []
    if cat_search:
        cat_where = "WHERE category_name ILIKE ?"
        cat_params = [f"%{cat_search}%"]

    df = _query(f"""
        SELECT
            category_name AS "Category",
            count(*) AS n,
            min(test_year) AS "First Year",
            max(test_year) AS "Latest Year",
            count(DISTINCT manufacturer_name) AS "Manufacturers"
        FROM curated.product_test_v
        {cat_where}
        GROUP BY category_name
        ORDER BY {order}
        LIMIT 100
    """, cat_params)

    st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "n": st.column_config.ProgressColumn(
                "Tests",
                min_value=0,
                max_value=int(df["n"].max()) if len(df) > 0 else 100,
                format="%d",
            ),
            "First Year": st.column_config.NumberColumn(format="%d"),
            "Latest Year": st.column_config.NumberColumn(format="%d"),
        },
    )

    # Category detail
    if len(df) > 0:
        st.divider()
        selected_cat = st.selectbox(
            "Drill into a category",
            df["Category"].tolist(),
            index=None,
            placeholder="Select a category for details…",
        )

        if selected_cat:
            st.markdown(f"##### {selected_cat}")

            detail = _query("""
                SELECT
                    product_name AS "Product",
                    manufacturer_name AS "Manufacturer",
                    test_year AS "Year",
                    session_set AS "Set",
                    own_label_or_brand AS "Type",
                    tier AS "Tier"
                FROM curated.product_test_v
                WHERE category_name = ?
                ORDER BY test_year DESC
                LIMIT 100
            """, [selected_cat])

            st.dataframe(detail, use_container_width=True, hide_index=True,
                         column_config={"Year": st.column_config.NumberColumn(format="%d")})


# ── tab: 2025 Sessions ───────────────────────────────────────────────

def _tab_sessions():
    st.markdown("#### 🗂️ 2025 Session Products")
    st.caption("59 products from Sets 26–32 with scores and awards.")

    col1, col2 = st.columns([1, 1])
    with col1:
        set_filter = st.selectbox(
            "Session Set",
            ["All", "Set 26", "Set 27", "Set 28", "Set 29", "Set 31", "Set 32"],
        )
    with col2:
        perf_filter = st.selectbox(
            "Performance",
            ["All", "Beat norm", "Below norm"],
        )

    conditions = []
    params = []
    if set_filter != "All":
        conditions.append("session_set = ?")
        params.append(set_filter)
    if perf_filter == "Beat norm":
        conditions.append("vs_category_norm > 0")
    elif perf_filter == "Below norm":
        conditions.append("vs_category_norm < 0")

    where = "WHERE " + " AND ".join(conditions) if conditions else ""

    df = _query(f"""
        SELECT
            product_name AS "Product",
            category_name AS "Category",
            session_set AS "Set",
            score_out_of_50 AS "Score /50",
            category_average AS "Cat Avg",
            vs_category_norm AS "vs Norm",
            CASE WHEN award_quality THEN '✅' ELSE '' END AS "Quality",
            CASE WHEN award_taste THEN '✅' ELSE '' END AS "Taste",
            CASE WHEN award_value THEN '✅' ELSE '' END AS "Value"
        FROM curated.session_report_v
        {where}
        ORDER BY score_out_of_50 DESC
    """, params)

    if len(df) == 0:
        st.info("No products match these filters.")
        return

    # Colour the vs Norm column
    st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Score /50": st.column_config.ProgressColumn(
                "Score /50",
                min_value=0,
                max_value=50,
                format="%d",
            ),
            "vs Norm": st.column_config.NumberColumn(
                "vs Norm",
                format="%+d",
            ),
        },
    )

    st.caption(f"{len(df)} products shown · ✅ = award on pack")


# ── tab: Measures ─────────────────────────────────────────────────────

def _tab_measures():
    st.markdown("#### 📐 Measure Explorer")
    st.caption("Browse the 43 measures in the FoodFax questionnaire.")

    df = _query("""
        SELECT
            measure_code AS "Code",
            measure_name AS "Measure",
            asked_of AS "Asked Of",
            count(DISTINCT product_test_id) AS "Products with data"
        FROM curated.measure_value_v
        WHERE variant = 'MEAN'
        GROUP BY measure_code, measure_name, asked_of
        ORDER BY measure_code
    """)

    asked_of_filter = st.selectbox(
        "Filter by audience",
        ["All", "All", "Drinks", "Fresh Produce"],
        index=0,
    )

    if asked_of_filter != "All":
        df = df[df["Asked Of"].str.contains(asked_of_filter, case=False, na=False)]

    st.dataframe(df, use_container_width=True, hide_index=True)

    # Measure detail
    st.divider()
    selected_measure = st.selectbox(
        "Drill into a measure",
        df["Measure"].tolist(),
        index=None,
        placeholder="Select a measure for stats…",
    )

    if selected_measure:
        st.markdown(f"##### {selected_measure} — Mean scores by year")

        trend = _query("""
            SELECT
                test_year AS year,
                round(avg(value), 2) AS avg_score,
                count(*) AS n
            FROM curated.measure_value_v
            WHERE measure_name = ? AND variant = 'MEAN'
              AND test_year >= 2010
            GROUP BY test_year
            ORDER BY test_year
        """, [selected_measure])

        if len(trend) > 1:
            st.line_chart(trend.set_index("year")["avg_score"])
            st.caption("Base sizes: " + ", ".join(
                f"{int(r['year'])}(n={r['n']})" for _, r in trend.iterrows()
            ))
        else:
            st.info("Not enough data points for a trend chart.")


# ── page entry point ──────────────────────────────────────────────────

def _tab_audit_log():
    st.markdown("#### 📋 Query Audit Log")
    st.caption("Every question asked and answer given, with traceability.")

    from phase5.audit_log import get_recent, get_stats

    stats = get_stats()
    if stats.get("total_queries", 0) > 0:
        c1, c2 = st.columns(2)
        c1.metric("Total queries", f"{stats['total_queries']:,}")
        c2.metric("Total tokens", f"{stats['total_tokens']:,}")
        st.divider()

    entries = get_recent(limit=100)
    if not entries:
        st.info("No queries logged yet. Ask a question in the Chat page to start the log.")
        return

    df = pd.DataFrame(entries)
    # Drop response-time column — not useful for display
    if "elapsed_s" in df.columns:
        df = df.drop(columns=["elapsed_s"])
    st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "timestamp": st.column_config.TextColumn("Time", width="medium"),
            "question": st.column_config.TextColumn("Question", width="large"),
            "answer": st.column_config.TextColumn("Answer (preview)", width="large"),
            "model": "Model",
            "tokens": "Tokens",
            "rounds": "Rounds",
        },
    )
    st.caption(f"Showing {len(entries)} most recent queries")


def render():
    """Main render function called by st.navigation."""
    tab1, tab2, tab3, tab4, tab5 = st.tabs([
        "🛒 Products",
        "📁 Categories",
        "🗂️ 2025 Sessions",
        "📐 Measures",
        "📋 Audit Log",
    ])

    with tab1:
        _tab_products()
    with tab2:
        _tab_categories()
    with tab3:
        _tab_sessions()
    with tab4:
        _tab_measures()
    with tab5:
        _tab_audit_log()
