"""
Phase 2 · Build the local DuckDB warehouse.

    python fis-gpt/run_phase2.py

Parses every data source profiled in Phase 1, melts measures to long form,
loads into a local DuckDB database, and verifies the golden-question counts.

Prerequisite: run_phase1.py must have run first (needs pdf_records.json, etc.)

Output: fis-gpt/out/fis_warehouse.duckdb
"""
from __future__ import annotations

import json
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

import pandas as pd
import config
from phase2 import (
    warehouse, parse_norm, parse_historic, parse_pre2021,
    load_pdfs, parse_crosstabs, load_measures, verify,
)


def main() -> int:
    config.ensure_out()

    print(f"\nF!S internal GPT — Phase 2 (warehouse build)")
    print(f"corpus: {config.CORPUS_ROOT}")
    print(f"output: {warehouse.DB_PATH}\n")

    t_total = time.time()
    failed = []

    # ── 0. Create schema ────────────────────────────────────────────────
    print("[schema]")
    t0 = time.time()
    # Remove old DB to start fresh
    if warehouse.DB_PATH.exists():
        warehouse.DB_PATH.unlink()
    con = warehouse.connect()
    warehouse.create_schema(con)
    print(f"  done in {time.time() - t0:.1f}s\n")

    # ── 1. Load measure dictionary ──────────────────────────────────────
    print("[measure dictionary]")
    t0 = time.time()
    try:
        measures_df = load_measures.build_measure_df()
        warehouse.load_df(con, "ffx.measure", measures_df)
        print(f"  {len(measures_df)} measures loaded in {time.time() - t0:.1f}s\n")
    except Exception:
        failed.append("measure dictionary")
        traceback.print_exc()
        print(f"  FAILED after {time.time() - t0:.1f}s\n")

    measure_lookup = load_measures.build_measure_id_lookup()

    # ── 2. Parse Norm Data ──────────────────────────────────────────────
    print("[Norm Data]")
    t0 = time.time()
    next_id = 1
    try:
        norm_products, norm_measures_raw, norm_stats = parse_norm.parse_norm_data()

        # Assign product_test_ids (global counter)
        norm_products.insert(0, "product_test_id", range(next_id, next_id + len(norm_products)))
        next_id += len(norm_products)

        warehouse.load_df(con, "ffx.product_test", norm_products)

        # Resolve measure names to IDs and build measure_value rows
        norm_mv = _build_measure_values(
            norm_measures_raw, measure_lookup, norm_products, "norm_data")
        if not norm_mv.empty:
            # Deduplicate: Norm Data has T2B% Better Than at two column positions
            before = len(norm_mv)
            norm_mv = norm_mv.drop_duplicates(
                subset=["product_test_id", "measure_id", "variant"], keep="first")
            if len(norm_mv) < before:
                print(f"    {before - len(norm_mv)} duplicate measure values removed")
            warehouse.load_df(con, "ffx.measure_value", norm_mv)
            print(f"    {len(norm_mv)} measure_value rows")

        # Load stats (deduplicate — Norm Data has T2B% Better Than twice)
        if not norm_stats.empty:
            norm_stats = norm_stats.drop_duplicates(
                subset=["column_name", "statistic"], keep="first")
            warehouse.load_df(con, "ffx.norm_column_stat", norm_stats)

        print(f"  done in {time.time() - t0:.1f}s\n")
    except Exception:
        failed.append("Norm Data")
        traceback.print_exc()
        print(f"  FAILED after {time.time() - t0:.1f}s\n")

    # ── 3. Parse Historic Products ──────────────────────────────────────
    print("[Historic Products]")
    t0 = time.time()
    try:
        historic_products = parse_historic.parse_historic_products()
        historic_products.insert(0, "product_test_id",
                                  range(next_id, next_id + len(historic_products)))
        next_id += len(historic_products)
        warehouse.load_df(con, "ffx.product_test", historic_products)
        print(f"  done in {time.time() - t0:.1f}s\n")
    except Exception:
        failed.append("Historic Products")
        traceback.print_exc()
        print(f"  FAILED after {time.time() - t0:.1f}s\n")

    # ── 4. Parse pre-2021 DB ────────────────────────────────────────────
    print("[pre-2021 DB]")
    t0 = time.time()
    try:
        pre21_products, pre21_measures_raw = parse_pre2021.parse_pre2021()
        pre21_products.insert(0, "product_test_id",
                               range(next_id, next_id + len(pre21_products)))
        next_id += len(pre21_products)
        warehouse.load_df(con, "ffx.product_test", pre21_products)

        pre21_mv = _build_measure_values(
            pre21_measures_raw, measure_lookup, pre21_products, "ffx_pre_2021")
        if not pre21_mv.empty:
            warehouse.load_df(con, "ffx.measure_value", pre21_mv)
            print(f"    {len(pre21_mv)} measure_value rows")

        print(f"  done in {time.time() - t0:.1f}s\n")
    except Exception:
        failed.append("pre-2021 DB")
        traceback.print_exc()
        print(f"  FAILED after {time.time() - t0:.1f}s\n")

    # ── 5. Load session PDFs ────────────────────────────────────────────
    print("[session PDFs]")
    t0 = time.time()
    try:
        pdf_products, pdf_reports, pdf_verbatims, pdf_awards = load_pdfs.load_pdf_records()

        pdf_products.insert(0, "product_test_id",
                             range(next_id, next_id + len(pdf_products)))
        pdf_id_start = next_id
        next_id += len(pdf_products)
        warehouse.load_df(con, "ffx.product_test", pdf_products)

        # session_report — add product_test_id
        pdf_reports.insert(0, "product_test_id",
                            range(pdf_id_start, pdf_id_start + len(pdf_reports)))
        warehouse.load_df(con, "ffx.session_report", pdf_reports)

        # verbatims — need to match cmr_ref to product_test_id
        if not pdf_verbatims.empty:
            # Build cmr_ref → product_test_id lookup from pdf_products
            cmr_to_ptid = dict(zip(
                pdf_products["cmr_ref"],
                pdf_products["product_test_id"]
            ))
            verb_rows = []
            verb_id = 1
            for _, row in pdf_verbatims.iterrows():
                ptid = cmr_to_ptid.get(str(row["cmr_ref"]))
                if ptid is None:
                    continue
                verb_rows.append({
                    "verbatim_id":     verb_id,
                    "product_test_id": ptid,
                    "stars":           row.get("stars"),
                    "text":            row["text"],
                    "pii_scrubbed":    True,
                    "source":          "session_pdf",
                })
                verb_id += 1
            if verb_rows:
                verb_df = pd.DataFrame(verb_rows)
                warehouse.load_df(con, "ffx.verbatim", verb_df)
                print(f"    {len(verb_df)} verbatims loaded")

        # category_award
        if not pdf_awards.empty:
            award_rows = []
            for _, row in pdf_awards.iterrows():
                ptid = pdf_id_start + int(row["product_row_idx"])
                award_rows.append({
                    "product_test_id": ptid,
                    "award_label":     row["award_label"],
                    "pct":             int(row["pct"]) if row["pct"] else 0,
                    "rank":            row.get("rank"),
                })
            if award_rows:
                awards_df = pd.DataFrame(award_rows)
                warehouse.load_df(con, "ffx.category_award", awards_df)
                print(f"    {len(awards_df)} category awards loaded")

        print(f"  done in {time.time() - t0:.1f}s\n")
    except Exception:
        failed.append("session PDFs")
        traceback.print_exc()
        print(f"  FAILED after {time.time() - t0:.1f}s\n")

    # ── 6. Parse banner crosstabs ───────────────────────────────────────
    print("[banner crosstabs]")
    t0 = time.time()
    try:
        crosstab_df = parse_crosstabs.parse_all_crosstabs()
        if not crosstab_df.empty:
            # Add IDs
            crosstab_df.insert(0, "crosstab_cell_id", range(1, len(crosstab_df) + 1))
            # Add product_test_id (None for now — requires OT Sheet cross-ref)
            crosstab_df["product_test_id"] = None
            # Reorder to match table schema
            schema_cols = [
                "crosstab_cell_id", "product_test_id", "cmr_ref",
                "question_text", "response_option",
                "banner_group", "banner_level", "banner_letter",
                "base_n", "pct", "sig_higher_than", "source_file",
            ]
            for c in schema_cols:
                if c not in crosstab_df.columns:
                    crosstab_df[c] = None
            warehouse.load_df(con, "ffx.crosstab_cell", crosstab_df[schema_cols])
            print(f"    {len(crosstab_df)} cells loaded")
        print(f"  done in {time.time() - t0:.1f}s\n")
    except Exception:
        failed.append("banner crosstabs")
        traceback.print_exc()
        print(f"  FAILED after {time.time() - t0:.1f}s\n")

    # ── 7. Load category norms ──────────────────────────────────────────
    print("[category norms]")
    t0 = time.time()
    try:
        norms_df = parse_norm.parse_current_norm_summary()
        if not norms_df.empty:
            warehouse.load_df(con, "ffx.category_norm", norms_df)
        print(f"  done in {time.time() - t0:.1f}s\n")
    except Exception:
        failed.append("category norms")
        traceback.print_exc()
        print(f"  FAILED after {time.time() - t0:.1f}s\n")

    # ── 8. Verify ───────────────────────────────────────────────────────
    print("[verification]")
    results = verify.run_all(con)

    # Save results
    (config.OUT_DIR / "phase2_verify.json").write_text(
        json.dumps(results, indent=2, default=str), encoding="utf-8")

    # ── Dashboard ───────────────────────────────────────────────────────
    print("\n" + "─" * 62)
    for table, label in [
        ("ffx.product_test",   "product tests"),
        ("ffx.measure",        "measures defined"),
        ("ffx.measure_value",  "measure values (melted)"),
        ("ffx.session_report", "session reports"),
        ("ffx.verbatim",       "verbatims"),
        ("ffx.crosstab_cell",  "crosstab cells"),
        ("ffx.category_norm",  "category norms"),
    ]:
        try:
            n = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        except Exception:
            n = "error"
        print(f"  {label:30s} {n}")
    print("─" * 62)

    summary = results.get("_summary", {})
    print(f"\n  verification: {summary.get('passed', 0)}/{summary.get('total', 0)} passed")
    print(f"  warehouse:    {warehouse.DB_PATH}")
    print(f"  total time:   {time.time() - t_total:.1f}s\n")

    con.close()

    if failed:
        print(f"STAGES FAILED: {', '.join(failed)}")
        return 1
    if summary.get("failed", 0) > 0:
        print(f"VERIFICATION FAILURES: {summary['failed']}")
        return 1
    return 0


def _build_measure_values(
    raw_df: pd.DataFrame,
    measure_lookup: dict[str, int],
    products_df: pd.DataFrame,
    source_table: str,
) -> pd.DataFrame:
    """
    Convert raw melted measures (with product_row_idx and measure_name)
    to warehouse measure_value rows (with product_test_id and measure_id).
    """
    if raw_df.empty:
        return pd.DataFrame()

    rows = []
    unresolved = set()

    for _, row in raw_df.iterrows():
        measure_name = row["measure_name"]
        mid = measure_lookup.get(measure_name)
        if mid is None:
            unresolved.add(measure_name)
            continue

        row_idx = int(row["product_row_idx"])
        if row_idx >= len(products_df):
            continue

        ptid = products_df.iloc[row_idx]["product_test_id"]
        variant = row["variant"]
        value = row["value"]

        rows.append({
            "product_test_id": ptid,
            "measure_id":      mid,
            "variant":         variant,
            "value":           value,
            "source_table":    source_table,
        })

    if unresolved:
        print(f"    {len(unresolved)} unresolved measure names: "
              f"{', '.join(sorted(unresolved)[:10])}"
              f"{'...' if len(unresolved) > 10 else ''}")

    return pd.DataFrame(rows)


if __name__ == "__main__":
    raise SystemExit(main())
