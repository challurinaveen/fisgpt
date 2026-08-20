"""
Phase 2 · Verification queries.

Runs the golden-question SQL against the loaded warehouse and checks that
the numbers match Phase 1 expectations. This is the exit gate for Phase 2.
"""
from __future__ import annotations

import duckdb

from phase2 import warehouse


def run_all(con: duckdb.DuckDBPyConnection) -> dict:
    """Run verification queries, return results dict."""
    results = {}
    checks_passed = 0
    checks_failed = 0

    def check(name: str, sql: str, expected, compare="eq"):
        nonlocal checks_passed, checks_failed
        try:
            actual = con.execute(sql).fetchone()
            val = actual[0] if actual else None
        except Exception as e:
            print(f"    FAIL  {name}: {e}")
            results[name] = {"status": "error", "error": str(e)}
            checks_failed += 1
            return

        if compare == "eq":
            ok = val == expected
        elif compare == "gte":
            ok = val is not None and val >= expected
        elif compare == "range":
            ok = val is not None and expected[0] <= val <= expected[1]
        else:
            ok = False

        status = "PASS" if ok else "FAIL"
        if ok:
            checks_passed += 1
        else:
            checks_failed += 1
        print(f"    {status}  {name}: got {val}, expected {expected}")
        results[name] = {"status": status, "actual": val, "expected": expected}

    print("  running verification queries...\n")

    # ── Product counts ──────────────────────────────────────────────────
    check(
        "total_products",
        "SELECT count(*) FROM curated.product_test_v",
        20048 + 59,  # norm + historic + pre2021 + session PDFs (some may overlap)
        compare="gte",  # at least this many; may be more if session PDFs add new
    )

    check(
        "norm_data_products",
        "SELECT count(*) FROM curated.product_test_v WHERE source_table = 'norm_data'",
        (6700, 6800),
        compare="range",
    )

    check(
        "historic_products",
        "SELECT count(*) FROM curated.product_test_v WHERE source_table = 'historic_products'",
        (13200, 13300),
        compare="range",
    )

    check(
        "pre2021_products",
        "SELECT count(*) FROM curated.product_test_v WHERE source_table = 'ffx_pre_2021'",
        (5700, 5900),
        compare="range",
    )

    check(
        "session_pdf_products",
        "SELECT count(*) FROM curated.product_test_v WHERE source_table = 'session_pdf'",
        59,
    )

    # ── Year coverage ───────────────────────────────────────────────────
    check(
        "min_year",
        "SELECT min(test_year) FROM curated.product_test_v WHERE test_year > 0",
        (2000, 2005),
        compare="range",
    )

    check(
        "max_year",
        "SELECT max(test_year) FROM curated.product_test_v",
        2025,
    )

    # ── Distinct dimensions ─────────────────────────────────────────────
    check(
        "distinct_categories",
        "SELECT count(DISTINCT category_name) FROM curated.product_test_v "
        "WHERE category_name IS NOT NULL",
        400,
        compare="gte",
    )

    check(
        "distinct_manufacturers",
        "SELECT count(DISTINCT manufacturer_name) FROM curated.product_test_v "
        "WHERE manufacturer_name IS NOT NULL",
        200,
        compare="gte",
    )

    # ── Measure values ──────────────────────────────────────────────────
    check(
        "measures_loaded",
        "SELECT count(DISTINCT measure_name) FROM ffx.measure",
        30,
        compare="gte",
    )

    check(
        "measure_values_count",
        "SELECT count(*) FROM ffx.measure_value",
        100000,
        compare="gte",
    )

    # ── Session reports ─────────────────────────────────────────────────
    check(
        "session_reports",
        "SELECT count(*) FROM ffx.session_report",
        59,
    )

    # ── Verbatims ───────────────────────────────────────────────────────
    check(
        "verbatims_count",
        "SELECT count(*) FROM ffx.verbatim",
        900,
        compare="gte",
    )

    # ── Golden question spot-checks ─────────────────────────────────────

    # M01: total product tests (norm + historic)
    check(
        "M01_total_tests",
        "SELECT count(*) FROM curated.product_test_v "
        "WHERE source_table IN ('norm_data', 'historic_products')",
        (19900, 20200),
        compare="range",
    )

    # M03: distinct categories
    check(
        "M03_distinct_categories",
        "SELECT count(DISTINCT category_name) FROM curated.product_test_v "
        "WHERE source_table IN ('norm_data', 'historic_products') "
        "AND category_name IS NOT NULL",
        400,
        compare="gte",
    )

    # M11: products beating category norm in 2025
    check(
        "M11_beating_norm_2025",
        "SELECT count(*) FROM curated.session_report_v "
        "WHERE vs_category_norm > 0",
        (10, 20),
        compare="range",
    )

    # M12: worst in Set 26 (Mac N Cheese Head at -19)
    try:
        row = con.execute("""
            SELECT product_name, vs_category_norm
            FROM curated.session_report_v
            WHERE session_set = 'Set 26'
            ORDER BY vs_category_norm ASC
            LIMIT 1
        """).fetchone()
        if row:
            ok = "Mac" in str(row[0]) and row[1] is not None and row[1] <= -15
            status = "PASS" if ok else "FAIL"
            if ok:
                checks_passed += 1
            else:
                checks_failed += 1
            print(f"    {status}  M12_worst_set26: {row[0]} at {row[1]}")
            results["M12_worst_set26"] = {"status": status, "actual": row}
        else:
            print("    FAIL  M12_worst_set26: no rows")
            checks_failed += 1
    except Exception as e:
        print(f"    FAIL  M12_worst_set26: {e}")
        checks_failed += 1

    print(f"\n  verification: {checks_passed} passed, {checks_failed} failed")
    results["_summary"] = {
        "passed": checks_passed,
        "failed": checks_failed,
        "total": checks_passed + checks_failed,
    }
    return results
