"""
Phase 1 · Session PDF template conformance check.

The build plan claims (§04) that the FoodFax session report is a stable enough
template for a deterministic parser, and that an LLM extraction pass is only
needed as a fallback. That is a claim, not a fact, until it is measured. This
module measures it.

Two things the first pass surfaced, both now handled:

  1. Five of the 59 reports have their PDF content stream written in a
     different object order, so pypdf's default (stream-order) extraction
     returns the verbatims before the identity block. `extraction_mode=
     "layout"` reconstructs true visual reading order and makes all 59
     structurally identical.

  2. Even within the identity line, field order is not fixed — some reports
     read "Price: ... Size: ..." and others "Size: ...Price: ..." with no
     separating space. Each field is therefore matched independently rather
     than as an ordered pair.

Output: pdf_records.json, pdf_records.csv, pdf_conformance.json
"""
from __future__ import annotations

import csv
import json
import logging
import re
import unicodedata
import warnings
from collections import Counter
from dataclasses import dataclass, asdict, field
from pathlib import Path

import pypdf

import config

warnings.filterwarnings("ignore")
logging.getLogger("pypdf").setLevel(logging.ERROR)   # "Rotated text discovered"

STAR_LEVELS = [5, 4, 3, 2, 1]
COL_SPLIT = re.compile(r"\s{2,}")

RE_CMR = re.compile(r"CMR\s*Ref:\s*(?P<cmr>\d{5,7})\s+(?P<session>\d+)-(?P<order>\d+)", re.I)
RE_PRICE = re.compile(r"Price:\s*[^\d]{0,4}(?P<price>\d+(?:\.\d+)?)", re.I)
RE_SIZE = re.compile(r"Size:\s*(?P<size>[\w.]+)", re.I)
RE_CAT_AWARDS = re.compile(r"^(?P<cat>.+?)\s+CATEGORY AWARDS\s*$", re.I)
RE_CAT_AVG = re.compile(r"Average for the category:\s*(?P<avg>\d+(?:\.\d+)?)", re.I)
RE_SCORE = re.compile(r"^(?P<score>\d{1,2})$")
RE_PCT_LEAD = re.compile(r"^(?P<pct>\d{1,3})%$")
RE_YESNO = re.compile(r"^(Yes|No)$", re.I)
RE_NUM_ROW = re.compile(r"^[\d.]+$")


@dataclass
class PdfRecord:
    file: str
    session_set: str
    cmr_ref: str = ""
    session_no: str = ""
    test_order: str = ""
    product_name: str = ""
    manufacturer: str = ""
    price_gbp: float | None = None
    size: str = ""
    score_out_of_50: int | None = None
    category_average: float | None = None
    vs_category_norm: float | None = None
    category: str = ""
    awards: list = field(default_factory=list)             # [{label, pct}]
    award_quality: str = ""
    award_taste: str = ""
    award_value: str = ""
    star_distribution: dict = field(default_factory=dict)  # {"5": 42, ...}
    verbatims: list = field(default_factory=list)          # [{stars, text}]
    nutrition_per_100g: dict = field(default_factory=dict)
    n_verbatims: int = 0
    missing_fields: list = field(default_factory=list)
    parse_ok: bool = False
    extraction_mode: str = "layout"


def _clean(t: str) -> str:
    t = t.replace("�", "'")
    t = unicodedata.normalize("NFKC", t)
    for bad, good in (("’", "'"), ("‘", "'"), ("“", '"'),
                      ("”", '"'), ("–", "-"), ("—", "-")):
        t = t.replace(bad, good)
    return t


def _layout_lines(path: Path) -> tuple[list[list[str]], str]:
    """Return each visual line as its list of column cells, plus mode used."""
    reader = pypdf.PdfReader(str(path))
    for mode in ("layout", "plain"):
        kwargs = {"extraction_mode": "layout"} if mode == "layout" else {}
        raw = "\n".join((p.extract_text(**kwargs) or "") for p in reader.pages)
        rows = []
        for line in raw.splitlines():
            line = _clean(line).rstrip()
            if not line.strip():
                continue
            cells = [c.strip() for c in COL_SPLIT.split(line.strip()) if c.strip()]
            if cells:
                rows.append(cells)
        if any("CMR Ref" in " ".join(r) for r in rows):
            return rows, mode
    return rows, "plain"


def parse_report(path: Path) -> PdfRecord:
    rec = PdfRecord(
        file=str(path.relative_to(config.CORPUS_ROOT)).replace("\\", "/"),
        session_set=path.parent.name,
    )
    rows, mode = _layout_lines(path)
    rec.extraction_mode = mode
    if not rows:
        rec.missing_fields = ["all"]
        return rec

    flat = ["  ".join(r) for r in rows]
    blob = "\n".join(flat)

    # --- identity -------------------------------------------------------
    for i, line in enumerate(flat):
        if "CMR Ref" in line:
            m = RE_CMR.search(line)
            if m:
                rec.cmr_ref, rec.session_no, rec.test_order = (
                    m.group("cmr"), m.group("session"), m.group("order"))
            mp = RE_PRICE.search(line)
            if mp:
                rec.price_gbp = float(mp.group("price"))
            ms = RE_SIZE.search(line)
            if ms:
                rec.size = ms.group("size")
            # The row above carries "Product Name | Manufacturer" — but a long
            # product name wraps, leaving one or more single-cell continuation
            # rows between it and the CMR line. Scan up for the two-cell row
            # and fold the wrapped fragments back onto the name.
            wrapped = []
            for j in range(i - 1, max(-1, i - 4), -1):
                row = rows[j]
                if len(row) >= 2:
                    rec.product_name = " ".join([row[0], *reversed(wrapped)]).strip()
                    rec.manufacturer = row[-1]
                    break
                if row:
                    wrapped.append(row[0])
            else:
                if wrapped:
                    rec.product_name = " ".join(reversed(wrapped)).strip()
            break

    # --- score, norm, category ------------------------------------------
    for i, r in enumerate(rows):
        if r and r[0].lower().startswith("score out of 50"):
            for j in range(i - 1, max(-1, i - 4), -1):
                m = RE_SCORE.match(rows[j][0])
                if m:
                    rec.score_out_of_50 = int(m.group("score"))
                    break
            break
    m = RE_CAT_AVG.search(blob)
    if m:
        rec.category_average = float(m.group("avg"))
    if rec.score_out_of_50 is not None and rec.category_average is not None:
        rec.vs_category_norm = round(rec.score_out_of_50 - rec.category_average, 1)

    awards_i = None
    for i, line in enumerate(flat):
        m = RE_CAT_AWARDS.match(line.strip())
        if m:
            rec.category = m.group("cat").strip()
            awards_i = i
            break

    # --- category awards: "Label | NN%" rows above the awards heading ----
    if awards_i is not None:
        for r in rows[max(0, awards_i - 6): awards_i]:
            cells = list(r)
            # the score cell can lead the row: ["41", "A good standby", "42%"]
            if cells and RE_SCORE.match(cells[0]):
                cells = cells[1:]
            if len(cells) >= 2 and RE_PCT_LEAD.match(cells[-1]):
                label = " ".join(cells[:-1]).strip()
                if label and not label.lower().startswith(("overall", "rating",
                                                           "score", "average")):
                    rec.awards.append(
                        {"label": label, "pct": int(cells[-1].rstrip("%"))})

    # --- on-pack marketing flags -----------------------------------------
    for i, r in enumerate(rows):
        upper = [c.upper() for c in r]
        if "QUALITY" in upper and "TASTE" in upper and "VALUE" in upper:
            if i + 1 < len(rows):
                nxt = [c for c in rows[i + 1] if RE_YESNO.match(c)]
                if len(nxt) >= 3:
                    rec.award_quality, rec.award_taste, rec.award_value = (
                        nxt[0].title(), nxt[1].title(), nxt[2].title())
            break

    # --- star distribution + verbatims ------------------------------------
    start = end = None
    for i, line in enumerate(flat):
        if start is None and "star rating" in line.lower():
            start = i + 1
        elif start is not None and line.lower().startswith("* reported verbatim"):
            end = i
            break
    if start is not None:
        end = end if end is not None else len(rows)
        block = rows[start:end]

        markers = []   # (row_index_within_block, pct)
        for bi, r in enumerate(block):
            if r and RE_PCT_LEAD.match(r[0]):
                markers.append((bi, int(r[0].rstrip("%"))))

        for k, (bi, pct) in enumerate(markers):
            if k < len(STAR_LEVELS):
                rec.star_distribution[str(STAR_LEVELS[k])] = pct

        # A verbatim belongs to the star bucket whose percentage marker is
        # vertically nearest — the label sits mid-block in the source layout.
        if markers:
            for bi, r in enumerate(block):
                cells = r[1:] if (r and RE_PCT_LEAD.match(r[0])) else list(r)
                nearest = min(range(len(markers)),
                              key=lambda k: (abs(markers[k][0] - bi), k))
                stars = STAR_LEVELS[nearest] if nearest < len(STAR_LEVELS) else None
                for cell in cells:
                    cell = cell.strip()
                    if len(cell) > 8 and not RE_PCT_LEAD.match(cell):
                        rec.verbatims.append({"stars": stars, "text": cell})
    rec.n_verbatims = len(rec.verbatims)

    # --- nutrition per 100g ------------------------------------------------
    NUTRI_KEYS = ["energy_kcal", "total_fat_g", "sat_fat_g", "carb_g",
                  "sugars_g", "fibre_g", "protein_g", "salt_g"]
    for i, line in enumerate(flat):
        if line.lower().startswith("nutrition per"):
            for r in rows[i + 1: i + 8]:
                nums = [c for c in r if RE_NUM_ROW.match(c)]
                if len(nums) >= 6:
                    rec.nutrition_per_100g = {
                        k: float(v) for k, v in zip(NUTRI_KEYS, nums)}
                    break
            break

    required = {
        "cmr_ref": rec.cmr_ref, "price_gbp": rec.price_gbp, "size": rec.size,
        "score_out_of_50": rec.score_out_of_50,
        "category_average": rec.category_average, "category": rec.category,
        "product_name": rec.product_name, "manufacturer": rec.manufacturer,
        "awards": rec.awards, "star_distribution": rec.star_distribution,
        "verbatims": rec.verbatims,
    }
    rec.missing_fields = [k for k, v in required.items() if not v]
    rec.parse_ok = not rec.missing_fields
    return rec


def target_pdfs() -> list[Path]:
    root = config.FFX_REPORTS
    if not root.exists():
        return []
    return sorted(p for p in root.rglob("*.pdf")
                  if not any(x in config.SKIP_DIR_NAMES for x in p.parts))


def run() -> dict:
    out = config.ensure_out()
    pdfs = target_pdfs()
    print(f"  parsing {len(pdfs)} session PDFs against the template...")

    records, errors = [], []
    for p in pdfs:
        try:
            records.append(parse_report(p))
        except Exception as e:                                # noqa: BLE001
            errors.append({"file": p.name, "error": f"{type(e).__name__}: {e}"})

    n = len(records) or 1
    fields = ["cmr_ref", "price_gbp", "size", "score_out_of_50",
              "category_average", "category", "product_name", "manufacturer",
              "awards", "star_distribution", "verbatims", "nutrition_per_100g",
              "award_quality"]
    rates = {f: {"extracted": sum(1 for r in records if getattr(r, f)),
                 "of": len(records),
                 "rate_pct": round(100 * sum(1 for r in records if getattr(r, f)) / n, 1)}
             for f in fields}

    star_sums = [sum(r.star_distribution.values()) for r in records if r.star_distribution]
    beat_norm = [r for r in records if r.vs_category_norm is not None and r.vs_category_norm > 0]

    summary = {
        "pdfs_parsed": len(records),
        "parse_errors": errors,
        "fully_conformant": sum(1 for r in records if r.parse_ok),
        "fully_conformant_pct": round(100 * sum(1 for r in records if r.parse_ok) / n, 1),
        "field_extraction_rates": rates,
        "integrity_star_pct_sums_to_100": sum(1 for s in star_sums if 98 <= s <= 102),
        "integrity_star_checked": len(star_sums),
        "total_verbatims": sum(r.n_verbatims for r in records),
        "verbatims_per_report_avg": round(sum(r.n_verbatims for r in records) / n, 1),
        "distinct_categories": len({r.category for r in records if r.category}),
        "distinct_cmr_refs": len({r.cmr_ref for r in records if r.cmr_ref}),
        "distinct_manufacturers": len({r.manufacturer for r in records if r.manufacturer}),
        "products_beating_category_norm": len(beat_norm),
        "sets_covered": sorted({r.session_set for r in records}),
        "common_missing_fields": dict(
            Counter(f for r in records for f in r.missing_fields).most_common()),
        "non_conformant_files": [
            {"file": r.file, "missing": r.missing_fields}
            for r in records if not r.parse_ok],
    }

    (out / "pdf_records.json").write_text(
        json.dumps([asdict(r) for r in records], indent=2), encoding="utf-8")
    (out / "pdf_conformance.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8")

    with (out / "pdf_records.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["cmr_ref", "session_set", "test_order", "product_name",
                    "manufacturer", "category", "price_gbp", "size",
                    "score_out_of_50", "category_average", "vs_category_norm",
                    "star_5", "star_4", "star_3", "star_2", "star_1",
                    "n_awards", "top_award", "top_award_pct",
                    "quality", "taste", "value", "n_verbatims", "parse_ok"])
        for r in records:
            sd = r.star_distribution
            w.writerow([r.cmr_ref, r.session_set, r.test_order, r.product_name,
                        r.manufacturer, r.category, r.price_gbp, r.size,
                        r.score_out_of_50, r.category_average, r.vs_category_norm,
                        sd.get("5", ""), sd.get("4", ""), sd.get("3", ""),
                        sd.get("2", ""), sd.get("1", ""),
                        len(r.awards),
                        r.awards[0]["label"] if r.awards else "",
                        r.awards[0]["pct"] if r.awards else "",
                        r.award_quality, r.award_taste, r.award_value,
                        r.n_verbatims, r.parse_ok])

    with (out / "verbatims.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["cmr_ref", "session_set", "product_name", "category", "stars", "verbatim"])
        for r in records:
            for v in r.verbatims:
                w.writerow([r.cmr_ref, r.session_set, r.product_name,
                            r.category, v["stars"], v["text"]])

    return summary


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
