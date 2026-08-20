"""
Phase 2 · Load session PDF records into the warehouse.

Reads the Phase 1 artefacts (pdf_records.json, verbatims.csv) and creates:
  - product_test rows for the 59 session products (linked by cmr_ref)
  - session_report rows with scores, stars, awards
  - verbatim rows
  - category_award rows
"""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path

import pandas as pd

import config


def load_pdf_records() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Returns (products_df, reports_df, verbatims_df, awards_df).
    """
    records_path = config.OUT_DIR / "pdf_records.json"
    verbatims_path = config.OUT_DIR / "verbatims.csv"

    if not records_path.exists():
        raise FileNotFoundError(
            f"Phase 1 artefact not found: {records_path}\n"
            "Run run_phase1.py first."
        )

    records = json.loads(records_path.read_text(encoding="utf-8"))
    print(f"  loading {len(records)} PDF records from Phase 1 artefacts...")

    # ── product_test rows ───────────────────────────────────────────────
    products = []
    reports = []
    awards_all = []

    for rec in records:
        cmr = rec.get("cmr_ref", "")
        session_set = rec.get("session_set", "")

        products.append({
            "unique_reference":   None,
            "cmr_ref":            cmr,
            "prodref":            cmr,
            "project_code":       None,
            "project_name":       None,
            "project_type":       "FFX",
            "product_name":       rec.get("product_name"),
            "category_code":      None,
            "category_name":      rec.get("category"),
            "manufacturer_code":  None,
            "manufacturer_name":  rec.get("manufacturer"),
            "own_label_or_brand": None,
            "test_year":          2025,  # Sets 26-32 are all 2025
            "test_date":          None,
            "session_set":        session_set,
            "test_order":         None,
            "price_gbp":          rec.get("price"),
            "pack_size":          rec.get("size"),
            "storage":            None,
            "sweet_or_savoury":   None,
            "fax_type":           None,
            "tier":               None,
            "sample_size":        None,
            "attribute_scale":    5,
            "source_table":       "session_pdf",
            "source_file":        rec.get("file", ""),
        })

        # session_report — placeholder product_test_id filled later
        score = rec.get("score_out_of_50")
        cat_avg = rec.get("category_average")
        stars = rec.get("star_distribution", {})

        reports.append({
            "score_out_of_50":  score,
            "category_average": cat_avg,
            "vs_category_norm": (score - cat_avg) if score and cat_avg else None,
            "star_5_pct":       stars.get("5_star"),
            "star_4_pct":       stars.get("4_star"),
            "star_3_pct":       stars.get("3_star"),
            "star_2_pct":       stars.get("2_star"),
            "star_1_pct":       stars.get("1_star"),
            "award_quality":    bool(rec.get("award_quality")),
            "award_taste":      bool(rec.get("award_taste")),
            "award_value":      bool(rec.get("award_value")),
        })

        # category_award — `awards` is a list of {label, pct} dicts
        for cat_award in (rec.get("awards") or []):
            awards_all.append({
                "product_row_idx": len(products) - 1,
                "award_label":     cat_award.get("label", ""),
                "pct":             cat_award.get("pct", 0),
                "rank":            cat_award.get("rank"),
            })

    products_df = pd.DataFrame(products)
    reports_df = pd.DataFrame(reports)
    awards_df = pd.DataFrame(awards_all) if awards_all else pd.DataFrame(
        columns=["product_row_idx", "award_label", "pct", "rank"])

    # ── verbatims ───────────────────────────────────────────────────────
    verbatims = []
    if verbatims_path.exists():
        with verbatims_path.open(encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                verbatims.append({
                    "cmr_ref":  row.get("cmr_ref", ""),
                    "stars":    _int_or_none(row.get("stars")),
                    "text":     row.get("text", ""),
                    "source":   "session_pdf",
                })

    verbatims_df = pd.DataFrame(verbatims) if verbatims else pd.DataFrame(
        columns=["cmr_ref", "stars", "text", "source"])

    print(f"    {len(products_df)} products, {len(reports_df)} reports, "
          f"{len(verbatims_df)} verbatims, {len(awards_df)} category awards")

    return products_df, reports_df, verbatims_df, awards_df


def _int_or_none(v):
    if v is None or v == "":
        return None
    try:
        return int(float(v))
    except (ValueError, TypeError):
        return None
