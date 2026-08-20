"""
Phase 3 · Document chunker.

Breaks the FoodFax corpus into retrieval-ready text chunks for the RAG
pipeline.  Each chunk carries structured metadata (source type, category,
year, CMR ref) so the answering layer can cite back to the original record.

Chunk types
-----------
product_report  – one per session-PDF product (59 chunks)
                  Combines score card, awards, star distribution, and nutrition
                  into a dense identity paragraph.

verbatim_group  – one per session-PDF product (up to 59 chunks)
                  All reviewer comments for a product, grouped by star rating,
                  preceded by a product context header.

measure_def     – one per FoodFax measure (36 chunks)
                  Explains what the question measures, how it's scored, and
                  which product categories it applies to.

category_norm   – one per active category (197 chunks)
                  Historical testing volume, premium / standard-value split,
                  and year-by-year breakdown.

Total: ~350 chunks — small enough for brute-force cosine similarity.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path

import config


@dataclass
class Chunk:
    """A single retrieval unit."""
    chunk_id:      int
    source_type:   str          # product_report | verbatim_group | measure_def | category_norm
    source_file:   str          # filename for citation
    cmr_ref:       str | None   # product reference (if applicable)
    category_name: str | None
    test_year:     int | None
    locator:       str          # human-readable locator for citation
    text:          str          # the chunk content


# ── builders ────────────────────────────────────────────────────────────

def build_product_report_chunks(
    records: list[dict], start_id: int = 1
) -> list[Chunk]:
    """One chunk per session-PDF product with score card and awards."""
    chunks = []
    cid = start_id
    for rec in records:
        name  = rec.get("product_name", "Unknown Product")
        mfr   = rec.get("manufacturer", "Unknown")
        cat   = rec.get("category", "")
        sset  = rec.get("session_set", "")
        cmr   = rec.get("cmr_ref")
        score = rec.get("score_out_of_50")
        avg   = rec.get("category_average")
        vs    = rec.get("vs_category_norm")
        price = rec.get("price_gbp")
        size  = rec.get("size", "")

        lines = [
            f"Product: {name}",
            f"Manufacturer: {mfr}",
            f"Category: {cat}",
            f"Session Set: {sset}",
        ]
        if cmr:
            lines.append(f"CMR Reference: {cmr}")
        if price:
            lines.append(f"Price: £{price}")
        if size:
            lines.append(f"Size: {size}")
        if score is not None:
            score_line = f"Score: {score}/50"
            if avg is not None:
                score_line += f" (category average: {avg})"
            if vs is not None:
                score_line += f" (vs norm: {vs:+.0f})" if isinstance(vs, (int, float)) else ""
            lines.append(score_line)

        # Star distribution
        stars = rec.get("star_distribution") or {}
        if stars:
            star_parts = []
            for s in ("5", "4", "3", "2", "1"):
                pct = stars.get(s, 0)
                star_parts.append(f"{'★' * int(s)} {pct}%")
            lines.append("Star Ratings: " + " | ".join(star_parts))

        # Awards
        award_flags = []
        if rec.get("award_quality"):
            award_flags.append("Quality Award")
        if rec.get("award_taste"):
            award_flags.append("Taste Award")
        if rec.get("award_value"):
            award_flags.append("Value Award")
        if award_flags:
            lines.append("Awards: " + ", ".join(award_flags))

        cat_awards = rec.get("awards") or []
        if cat_awards:
            award_strs = [f"{a['label']} ({a['pct']}%)" for a in cat_awards if a.get("label")]
            if award_strs:
                lines.append("Category Awards: " + ", ".join(award_strs))

        # Nutrition
        nut = rec.get("nutrition_per_100g") or {}
        if nut:
            nut_parts = []
            for key, label in [
                ("energy_kcal", "Energy"), ("total_fat_g", "Fat"),
                ("sat_fat_g", "Sat Fat"), ("carb_g", "Carbs"),
                ("sugars_g", "Sugars"), ("fibre_g", "Fibre"),
                ("protein_g", "Protein"), ("salt_g", "Salt"),
            ]:
                v = nut.get(key)
                if v is not None:
                    unit = "kcal" if "kcal" in key else "g"
                    nut_parts.append(f"{label}: {v}{unit}")
            if nut_parts:
                lines.append("Nutrition per 100g: " + ", ".join(nut_parts))

        lines.append(f"Source: {rec.get('file', 'session PDF')}")

        text = "\n".join(lines)
        chunks.append(Chunk(
            chunk_id=cid,
            source_type="product_report",
            source_file=rec.get("file", ""),
            cmr_ref=str(cmr) if cmr else None,
            category_name=cat or None,
            test_year=None,
            locator=f"{name} ({sset})" if sset else name,
            text=text,
        ))
        cid += 1

    return chunks


def build_verbatim_chunks(
    records: list[dict], start_id: int = 100
) -> list[Chunk]:
    """One chunk per product with all reviewer comments grouped by star rating."""
    chunks = []
    cid = start_id
    for rec in records:
        verbatims = rec.get("verbatims") or []
        if not verbatims:
            continue

        name  = rec.get("product_name", "Unknown Product")
        cmr   = rec.get("cmr_ref")
        cat   = rec.get("category", "")
        sset  = rec.get("session_set", "")
        score = rec.get("score_out_of_50")

        header = f"Reviewer Comments for {name}"
        if cmr:
            header += f" (CMR {cmr})"
        context = f"Category: {cat}"
        if sset:
            context += f" | Set: {sset}"
        if score is not None:
            context += f" | Score: {score}/50"

        lines = [header, context, ""]

        # Group verbatims by star rating
        by_stars: dict[int, list[str]] = {}
        for v in verbatims:
            s = v.get("stars", 0)
            by_stars.setdefault(s, []).append(v.get("text", ""))

        for star_val in sorted(by_stars.keys(), reverse=True):
            star_label = "★" * star_val if star_val > 0 else "Unrated"
            lines.append(f"{star_label} Comments:")
            for text in by_stars[star_val]:
                lines.append(f'  - "{text}"')
            lines.append("")

        lines.append(f"Source: {rec.get('file', 'session PDF')}")

        text = "\n".join(lines)
        chunks.append(Chunk(
            chunk_id=cid,
            source_type="verbatim_group",
            source_file=rec.get("file", ""),
            cmr_ref=str(cmr) if cmr else None,
            category_name=cat or None,
            test_year=None,
            locator=f"Verbatims – {name}",
            text=text,
        ))
        cid += 1

    return chunks


def build_measure_def_chunks(
    measures: list[dict], start_id: int = 200
) -> list[Chunk]:
    """One chunk per measure from the FoodFax question dictionary."""
    chunks = []
    cid = start_id
    for m in measures:
        code = m.get("question_code", "")
        name = m.get("question", "")
        raw  = m.get("question_raw", "")
        asked = m.get("asked_of", "All")
        stats = m.get("statistics_raw", "")
        reports = m.get("reports_needed_for", "")
        clients = m.get("clients", "")
        variants = m.get("variants_available") or []

        lines = [
            f"FoodFax Measure: {raw or name}",
            f"Code: {code}",
            f"Asked of: {asked}",
        ]
        if stats:
            lines.append(f"Statistics reported: {stats}")
        if variants:
            lines.append(f"Variants: {', '.join(variants)}")
        if reports:
            lines.append(f"Reports needed for: {reports}")
        if clients:
            lines.append(f"Clients: {clients}")

        # Physical columns info
        phys = m.get("physical_columns") or []
        if phys:
            tables = set()
            for pc in phys:
                tables.add(pc.get("table", "unknown"))
            lines.append(f"Available in: {', '.join(sorted(tables))}")

        lines.append("Source: Foodfax Database details.xlsx")

        text = "\n".join(lines)
        chunks.append(Chunk(
            chunk_id=cid,
            source_type="measure_def",
            source_file="Foodfax Database details.xlsx",
            cmr_ref=None,
            category_name=None,
            test_year=None,
            locator=f"Measure {code}: {name}",
            text=text,
        ))
        cid += 1

    return chunks


def build_category_norm_chunks(
    con, start_id: int = 300
) -> list[Chunk]:
    """One chunk per category from the Current Norm Summary."""
    rows = con.execute("""
        SELECT category_name, total, premium, standard_value,
               total_last_5y, pct_last_5y,
               y2024, y2023, y2022, y2021, y2020, y2019, y2018, y2017, y2016
        FROM ffx.category_norm
        WHERE category_name IS NOT NULL
        ORDER BY total DESC NULLS LAST
    """).fetchall()

    chunks = []
    cid = start_id
    for row in rows:
        cat = row[0]
        total = row[1]
        premium = row[2]
        std_val = row[3]
        last5 = row[4]
        pct5 = row[5]
        yearly = list(zip(
            [2024, 2023, 2022, 2021, 2020, 2019, 2018, 2017, 2016],
            row[6:]
        ))

        lines = [
            f"Category Norm: {cat}",
            f"Total products tested: {total or 0}",
        ]
        if premium is not None or std_val is not None:
            lines.append(f"Premium: {premium or 0} | Standard/Value: {std_val or 0}")
        if last5 is not None:
            pct_str = f" ({pct5:.0f}%)" if pct5 else ""
            lines.append(f"Last 5 years: {last5}{pct_str}")

        yr_parts = []
        for yr, n in yearly:
            if n is not None and n > 0:
                yr_parts.append(f"{yr}: {n}")
        if yr_parts:
            lines.append("By year: " + ", ".join(yr_parts))

        lines.append("Source: Foodfax Database ARCHIVE.xlsm (Current Norm Summary)")

        text = "\n".join(lines)
        chunks.append(Chunk(
            chunk_id=cid,
            source_type="category_norm",
            source_file="Foodfax Database ARCHIVE.xlsm",
            cmr_ref=None,
            category_name=cat,
            test_year=None,
            locator=f"Category Norm – {cat}",
            text=text,
        ))
        cid += 1

    return chunks


def build_methodology_chunk(start_id: int = 4000) -> list[Chunk]:
    """
    A single chunk explaining the FoodFax methodology and report templates.
    Answers D08, D11 type questions about how FoodFax works.
    """
    text = """\
FoodFax Methodology and Report Templates

FoodFax is the product testing service run by F!S Group (Food Insight & Strategy).
Each FoodFax session tests a set of food and drink products through a consumer panel.

Score out of 50: The headline metric. It is a weighted composite of multiple
measure scores (Taste, Appearance, Packaging, Value for Money, etc.) — NOT a
simple average. Each product is compared against its category norm to determine
whether it is beating or trailing the category average.

Star Ratings: Panellists independently rate products 1-5 stars. This is separate
from the score out of 50. The star distribution shows the spread of opinion.

Report Templates produced for each FoodFax session:
  - Short Reports: one-page summary per product with score, stars, awards
  - Full Reports: detailed multi-page report with all measure breakdowns
  - Tables (FFX PT Tables): banner cross-tabulations by demographics
    (gender, age, region, household size, presence of children, etc.)
  - Awards Summary: which products earned Quality, Taste, or Value awards
  - Verbatims: all reviewer comments grouped by star rating
  - OT Sheet: data entry sheet with raw scores
  - FFX DATA: the measure scores for each product
  - References: product images and packaging shots

The Tables workbooks (e.g. "Foodfax PT Tables Set 26.xlsx") contain banner
cross-tabulations showing how different demographic groups responded to each
question. Each column represents a demographic level (e.g. "Male A n=11")
with significance testing letters for statistical comparisons.

Attribute scales: Products are scored on either a 5-point or 7-point scale
depending on the era. Scores across different scales should NOT be directly
compared without adjustment.

Source: Foodfax Database details.xlsx, REPORT TEMPLATES"""

    return [Chunk(
        chunk_id=start_id,
        source_type="methodology",
        source_file="Foodfax Database details.xlsx",
        cmr_ref=None,
        category_name=None,
        test_year=None,
        locator="FoodFax Methodology & REPORT TEMPLATES",
        text=text,
    )]


def build_crosstab_summary_chunks(con, start_id: int = 5000) -> list[Chunk]:
    """
    One chunk per session set summarising which crosstab questions were asked
    and what banner groups are available. Enables citation of FFX Tables workbooks.
    """
    rows = con.execute("""
        SELECT DISTINCT source_file
        FROM ffx.crosstab_cell
        WHERE source_file IS NOT NULL
        ORDER BY source_file
    """).fetchall()

    chunks = []
    cid = start_id
    for (source_file,) in rows:
        # Get summary for this workbook
        stats = con.execute("""
            SELECT
                count(*) AS total_cells,
                count(DISTINCT question_text) AS n_questions,
                count(DISTINCT banner_group) AS n_banner_groups,
                count(DISTINCT cmr_ref) AS n_products
            FROM ffx.crosstab_cell
            WHERE source_file = ?
        """, [source_file]).fetchone()

        questions = con.execute("""
            SELECT DISTINCT question_text
            FROM ffx.crosstab_cell
            WHERE source_file = ?
            ORDER BY question_text
            LIMIT 20
        """, [source_file]).fetchall()

        banners = con.execute("""
            SELECT DISTINCT banner_group
            FROM ffx.crosstab_cell
            WHERE source_file = ?
            ORDER BY banner_group
        """, [source_file]).fetchall()

        import os
        filename = os.path.basename(source_file) if source_file else source_file

        lines = [
            f"Crosstab Tables: {filename}",
            f"Total cells: {stats[0]:,}",
            f"Questions covered: {stats[1]}",
            f"Banner groups: {stats[2]}",
            f"Products covered: {stats[3]}",
            "",
            "Questions include:",
        ]
        for (q,) in questions:
            lines.append(f"  - {q}")

        lines.append("")
        lines.append("Banner groups:")
        for (b,) in banners:
            lines.append(f"  - {b}")

        lines.append("")
        lines.append("These Tables show how different demographic groups responded")
        lines.append("to each question, with significance testing and base sizes.")
        lines.append("Use this data to find preference reasons, demographic over-indexing,")
        lines.append("and response patterns by gender, age, region, etc.")
        lines.append(f"Source: {filename}")

        text = "\n".join(lines)
        chunks.append(Chunk(
            chunk_id=cid,
            source_type="crosstab_summary",
            source_file=filename,
            cmr_ref=None,
            category_name=None,
            test_year=None,
            locator=f"Crosstab Tables – {filename}",
            text=text,
        ))
        cid += 1

    return chunks


# ── orchestration ───────────────────────────────────────────────────────

def build_all_chunks(con) -> list[Chunk]:
    """Build all chunk types and return a flat list."""
    out_dir = config.OUT_DIR

    # Load PDF records
    with open(out_dir / "pdf_records.json", "r", encoding="utf-8") as f:
        pdf_records = json.load(f)

    # Load measure dictionary
    with open(out_dir / "measure_dictionary.json", "r", encoding="utf-8") as f:
        measure_dict = json.load(f)
    measures = measure_dict.get("measures", [])

    chunks: list[Chunk] = []

    # 1. Product report chunks
    product_chunks = build_product_report_chunks(pdf_records, start_id=1)
    print(f"    product_report chunks: {len(product_chunks)}")
    chunks.extend(product_chunks)

    # 2. Verbatim group chunks
    verbatim_chunks = build_verbatim_chunks(pdf_records, start_id=1000)
    print(f"    verbatim_group chunks: {len(verbatim_chunks)}")
    chunks.extend(verbatim_chunks)

    # 3. Measure definition chunks
    measure_chunks = build_measure_def_chunks(measures, start_id=2000)
    print(f"    measure_def chunks:    {len(measure_chunks)}")
    chunks.extend(measure_chunks)

    # 4. Category norm chunks (from DuckDB)
    norm_chunks = build_category_norm_chunks(con, start_id=3000)
    print(f"    category_norm chunks:  {len(norm_chunks)}")
    chunks.extend(norm_chunks)

    # 5. Methodology chunk
    method_chunks = build_methodology_chunk(start_id=4000)
    print(f"    methodology chunks:    {len(method_chunks)}")
    chunks.extend(method_chunks)

    # 6. Crosstab summary chunks (from DuckDB)
    xtab_chunks = build_crosstab_summary_chunks(con, start_id=5000)
    print(f"    crosstab_summary:      {len(xtab_chunks)}")
    chunks.extend(xtab_chunks)

    return chunks


def chunks_to_dicts(chunks: list[Chunk]) -> list[dict]:
    """Convert chunks to plain dicts for JSON serialization."""
    return [asdict(c) for c in chunks]
