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

    # Filters — row 1
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
        tier_filter = st.selectbox("Tier", ["All", "Premium", "Standard", "Value"])

    # Filters — row 2
    col5, col6 = st.columns(2)

    with col5:
        mfrs = _query("""
            SELECT DISTINCT manufacturer_name
            FROM curated.product_test_v
            WHERE manufacturer_name IS NOT NULL
            ORDER BY manufacturer_name
        """)
        mfr_options = ["All"] + mfrs["manufacturer_name"].tolist()
        mfr_filter = st.selectbox("Manufacturer", mfr_options)
    with col6:
        cats = _query("""
            SELECT DISTINCT category_name
            FROM curated.product_test_v
            ORDER BY category_name
        """)
        cat_options = ["All"] + cats["category_name"].tolist()
        cat_filter = st.selectbox("Category", cat_options)

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
    if tier_filter != "All":
        conditions.append("tier = ?")
        params.append(tier_filter)
    if mfr_filter != "All":
        conditions.append("manufacturer_name = ?")
        params.append(mfr_filter)
    if cat_filter != "All":
        conditions.append("category_name = ?")
        params.append(cat_filter)

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
                own_label_or_brand AS type,
                tier,
                price_gbp AS price
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

    # Filters
    col1, col2, col3, col4 = st.columns([2, 1, 1, 1])
    with col1:
        cat_search = st.text_input("🔍 Search categories", placeholder="e.g. Chocolate")
    with col2:
        sort_by = st.selectbox("Sort by", ["Most tested", "Least tested", "A-Z"])
    with col3:
        cat_mfrs = _query("""
            SELECT DISTINCT manufacturer_name
            FROM curated.product_test_v
            WHERE manufacturer_name IS NOT NULL
            ORDER BY manufacturer_name
        """)
        cat_mfr_options = ["All manufacturers"] + cat_mfrs["manufacturer_name"].tolist()
        cat_mfr_filter = st.selectbox("Manufacturer", cat_mfr_options, key="cat_mfr")
    with col4:
        cat_tier_filter = st.selectbox("Tier", ["All tiers", "Premium", "Standard", "Value"],
                                       key="cat_tier")

    order = {
        "Most tested": "n DESC",
        "Least tested": "n ASC",
        "A-Z": "category_name ASC",
    }[sort_by]

    cat_conditions = []
    cat_params = []
    if cat_search:
        cat_conditions.append("category_name ILIKE ?")
        cat_params.append(f"%{cat_search}%")
    if cat_mfr_filter != "All manufacturers":
        cat_conditions.append("manufacturer_name = ?")
        cat_params.append(cat_mfr_filter)
    if cat_tier_filter != "All tiers":
        cat_conditions.append("tier = ?")
        cat_params.append(cat_tier_filter)

    cat_where = "WHERE " + " AND ".join(cat_conditions) if cat_conditions else ""

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

    st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Score /50": st.column_config.NumberColumn("Score /50", format="%d"),
            "Cat Avg": st.column_config.NumberColumn("Cat Avg", format="%d"),
            "vs Norm": st.column_config.NumberColumn("vs Norm", format="%+d"),
        },
    )

    st.caption(f"{len(df)} products shown · ✅ = award on pack")


# ── tab: Measures ─────────────────────────────────────────────────────

def _tab_measures():
    st.markdown("#### 📐 Measure Explorer")
    st.caption("Browse the 43 measures in the FoodFax questionnaire. "
               "Filter by category to see how a specific category scores.")

    # Filters
    col1, col2 = st.columns(2)

    with col1:
        asked_of_filter = st.selectbox(
            "Filter by audience",
            ["All", "Drinks", "Fresh Produce"],
            index=0,
            key="measure_audience",
        )
    with col2:
        measure_cats = _query("""
            SELECT DISTINCT category_name
            FROM curated.measure_value_v
            ORDER BY category_name
        """)
        measure_cat_options = ["All categories"] + measure_cats["category_name"].tolist()
        measure_cat_filter = st.selectbox(
            "Filter by category",
            measure_cat_options,
            key="measure_category",
        )

    # Build measure list query — optionally scoped to a category
    if measure_cat_filter != "All categories":
        df = _query("""
            SELECT
                measure_code AS "Code",
                measure_name AS "Measure",
                asked_of AS "Asked Of",
                count(DISTINCT product_test_id) AS "Products",
                round(avg(value), 2) AS "Avg Score"
            FROM curated.measure_value_v
            WHERE variant = 'MEAN' AND category_name = ?
            GROUP BY measure_code, measure_name, asked_of
            ORDER BY measure_code
        """, [measure_cat_filter])
    else:
        df = _query("""
            SELECT
                measure_code AS "Code",
                measure_name AS "Measure",
                asked_of AS "Asked Of",
                count(DISTINCT product_test_id) AS "Products"
            FROM curated.measure_value_v
            WHERE variant = 'MEAN'
            GROUP BY measure_code, measure_name, asked_of
            ORDER BY measure_code
        """)

    if asked_of_filter != "All":
        df = df[df["Asked Of"].str.contains(asked_of_filter, case=False, na=False)]

    st.dataframe(df, use_container_width=True, hide_index=True)

    # Measure detail — trend chart
    st.divider()
    selected_measure = st.selectbox(
        "Drill into a measure",
        df["Measure"].tolist(),
        index=None,
        placeholder="Select a measure for trend chart…",
        key="measure_drill",
    )

    if selected_measure:
        if measure_cat_filter != "All categories":
            st.markdown(f"##### {selected_measure} — {measure_cat_filter}")

            trend = _query("""
                SELECT
                    test_year AS year,
                    round(avg(value), 2) AS avg_score,
                    count(*) AS n
                FROM curated.measure_value_v
                WHERE measure_name = ? AND variant = 'MEAN'
                  AND category_name = ?
                  AND test_year >= 2010
                GROUP BY test_year
                ORDER BY test_year
            """, [selected_measure, measure_cat_filter])
        else:
            st.markdown(f"##### {selected_measure} — All categories")

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


# ── tab: Compare (product vs norm) ──────────────────────────────────

def _tab_compare():
    st.markdown("#### 📊 Product vs Category Norm")
    st.caption("Select products and see how they perform against their category averages.")

    # Pick category first to narrow the product list
    compare_cats = _query("""
        SELECT DISTINCT category_name
        FROM curated.session_report_v
        ORDER BY category_name
    """)

    if len(compare_cats) == 0:
        st.info("No session report data available for comparison.")
        return

    selected_category = st.selectbox(
        "Select a category",
        compare_cats["category_name"].tolist(),
        index=None,
        placeholder="Choose a category…",
        key="compare_category",
    )

    if not selected_category:
        st.info("Pick a category above to see product comparisons.")
        return

    products_in_cat = _query("""
        SELECT
            product_name,
            score_out_of_50,
            category_average,
            vs_category_norm
        FROM curated.session_report_v
        WHERE category_name = ?
        ORDER BY product_name
    """, [selected_category])

    if len(products_in_cat) == 0:
        st.info(f"No session report products found in '{selected_category}'.")
        return

    selected_products = st.multiselect(
        "Select product(s) to compare",
        products_in_cat["product_name"].tolist(),
        default=products_in_cat["product_name"].tolist(),
        key="compare_products",
    )

    if not selected_products:
        st.info("Select at least one product.")
        return

    df = products_in_cat[products_in_cat["product_name"].isin(selected_products)].copy()

    # ── Score vs Category Average ──
    st.divider()
    st.markdown(f"##### Score vs Category Average — {selected_category}")

    # Build a clean table for display with conditional colouring
    display_df = pd.DataFrame({
        "Product": df["product_name"].values,
        "Score /50": df["score_out_of_50"].values,
        "Cat Avg": df["category_average"].values,
        "vs Norm": df["vs_category_norm"].values,
    })

    st.dataframe(
        display_df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Product": st.column_config.TextColumn("Product", width="large"),
            "Score /50": st.column_config.NumberColumn("Score /50", format="%d"),
            "Cat Avg": st.column_config.NumberColumn("Cat Avg", format="%d"),
            "vs Norm": st.column_config.NumberColumn("vs Norm", format="%+d"),
        },
    )

    # Bar chart — grouped bars per product
    if len(df) <= 15:
        chart_data = pd.DataFrame({
            "Product": df["product_name"].values,
            "Product Score": df["score_out_of_50"].values,
            "Category Average": df["category_average"].values,
        }).set_index("Product")

        st.bar_chart(chart_data, horizontal=True)
        st.caption("Bars show the product's score out of 50 alongside the category average.")

    # ── Measure-level breakdown (if products selected) ──
    if len(selected_products) <= 5:
        st.divider()
        st.markdown("##### Measure Breakdown")

        # Get key measures for the selected products
        # NOTE: 'Overall Impression / Quality' is the correct name, NOT 'Overall Impression'
        placeholders = ", ".join(["?"] * len(selected_products))
        measures_df = _query(f"""
            SELECT
                product_name,
                measure_name,
                round(value, 2) AS score
            FROM curated.measure_value_v
            WHERE product_name IN ({placeholders})
              AND variant = 'MEAN'
              AND measure_name IN (
                  'Taste', 'Overall Impression / Quality', 'Value for Money',
                  'Initial Appeal', 'Appearance', 'Packaging',
                  'Smell', 'Texture'
              )
            ORDER BY measure_name, product_name
        """, selected_products)

        if len(measures_df) > 0:
            pivot = measures_df.pivot_table(
                index="measure_name", columns="product_name",
                values="score", aggfunc="first",
            )
            st.bar_chart(pivot, horizontal=True)
            st.caption("Key measures compared across selected products (mean scores).")
        else:
            st.info("No measure-level data available for these products.")


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
    tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
        "🛒 Products",
        "📁 Categories",
        "🗂️ 2025 Sessions",
        "📐 Measures",
        "📊 Compare",
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
        _tab_compare()
    with tab6:
        _tab_audit_log()
