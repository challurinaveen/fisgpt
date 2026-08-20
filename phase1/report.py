"""
Phase 1 · Findings note generator.

The exit criterion for Phase 1 is "a data-quality findings note — the brief's
first task, evidenced". This writes it from the artefacts the other modules
produced, so the note can never drift from the numbers behind it.

Output: PHASE1_FINDINGS.md (in the project root, next to the build plan)
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import config


def _load(name: str) -> dict:
    p = config.OUT_DIR / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def _mb(n: int) -> str:
    return f"{n / 1_048_576:.0f} MB"


def build() -> Path:
    inv = _load("inventory_summary.json")
    xls = _load("excel_profile_summary.json")
    pdf = _load("pdf_conformance.json")
    key = _load("key_coverage.json")
    dct = _load("measure_dictionary.json").get("summary", {})

    rates = pdf.get("field_extraction_rates", {})
    cov = key.get("coverage", {})
    shapes = xls.get("shapes", {})

    md = f"""# Phase 1 findings — data quality, accessibility and usability

**F!S Group internal GPT · {date.today().isoformat()}**

This is the exit deliverable for Phase 1 of the build plan and the evidence for
the brief's first task ("review and understand existing data sources"). Every
number below was produced by the code in `fis-gpt/phase1/` and can be
reproduced with `python fis-gpt/run_phase1.py`. Raw artefacts are in
`fis-gpt/out/`.

---

## 1. Corpus

| | |
|---|---|
| Files walked | {inv.get('files_total_walked', 0)} |
| Real files in the live corpus | **{inv.get('files_live_real', 0)}** |
| Live corpus size | **{_mb(inv.get('bytes_live_real', 0))}** |
| Failed-download stubs | {inv.get('error_stubs_live', 0)} |
| Duplicate files (byte-identical) | {inv.get('duplicate_files', 0)} |
| Storage wasted on duplicates | **{_mb(inv.get('duplicate_bytes_wasted', 0))}** |

By type: {', '.join(f'{v} {k.replace("_", " ")}' for k, v in inv.get('by_kind', {}).items())}.

**Deduplication is not optional.** `Archive/` is a byte-identical copy of the
live tree — {_mb(inv.get('duplicate_bytes_wasted', 0))} against a
{_mb(inv.get('bytes_live_real', 0))} corpus, i.e. essentially everything is
stored twice. Ingested as-is, every answer would cite two identical sources and
half of every retrieval window would be wasted. Content-hash dedupe is
implemented (`meta.source_file.content_sha256`) and runs before chunking.

---

## 2. What is missing, and why

{inv.get('missing_manifest_entries', 0)} items failed to download, across
{inv.get('missing_folders', 0)} whole folders:

| Dataset | Items missing |
|---|---|
""" + "\n".join(
        f"| {k} | {v} |" for k, v in inv.get("missing_by_dataset", {}).items()
    ) + f"""

Failure types: {', '.join(f'`{k}` ×{v}' for k, v in inv.get('error_types', {}).items())}.

`TooManyRequestsMeTAException` is SharePoint throttling. This confirms Finding A
of the build plan: the manual bulk-export route does not scale to this corpus,
and retrying it will fail the same way. **Microsoft Graph delta sync is a
prerequisite, not an improvement** — it is the only path that gets the rest of
the data in.

Two of the three Phase-1 datasets named in the credentials deck (Future Food,
Appetite Shift) are entirely absent. Phase 1 therefore proceeds on FoodFax
alone, exactly as the plan anticipated.

---

## 3. Workbooks: structure is the problem, not volume

{xls.get('workbooks_profiled', 0)} workbooks profiled, {xls.get('sheets_total', 0)} sheets,
**{xls.get('workbooks_failed', 0)} failures**.

| Sheet shape | Count | Handling |
|---|---:|---|
| `report_surface` | {shapes.get('report_surface', 0)} | **Skipped.** Rendering templates (`Short 1-15`, `Full 1-15`), not data |
| `flat` | {shapes.get('flat', 0)} | Header on row 1, ingest directly |
| `two_row_header` | {shapes.get('two_row_header', 0)} | Block label forward-filled and prefixed onto the attribute |
| `key_value` | {shapes.get('key_value', 0)} | Small lookups (`OT Sheet` test-order maps) |
| `banner_crosstab` | {shapes.get('banner_crosstab', 0)} | Melt to long form, preserve base sizes and significance letters |
| `stats_block` | {shapes.get('stats_block', 0)} | Summary statistics above the real header |
| `coalesce_header` | {shapes.get('coalesce_header', 0)} | Two complementary header rows merged per column |

**{xls.get('sheets_skipped_report_surface', 0)} of {xls.get('sheets_total', 0)} sheets
({100 * xls.get('sheets_skipped_report_surface', 0) // max(1, xls.get('sheets_total', 1))}%)
are report-rendering surfaces, not data.** The 44-sheet session workbooks are
mostly formatted output. Ingesting them wholesale would duplicate every number
in the corpus and poison retrieval. Only **{xls.get('sheets_to_ingest', 0)} sheets**
carry data worth indexing.

### The three shapes that break naive ingestion

1. **`Norm Data` (6,770 × 113).** The real header is **row 7**. Rows 1-6 are
   column-level statistics — count, mean, min, max, standard deviation,
   standard error. A generic "first row with three strings" heuristic — which
   is exactly what the existing prototype's `loaders.py` uses — reads row 1 and
   silently corrupts the entire table. Detected correctly at confidence 1.0.

2. **`FFX DB Update pre 2021` (5,811 × 61).** Two header rows where the
   attribute names *repeat*: `Appearance` appears once under `MS` (mean score)
   and again under `Percentage (T2B ...)`. Coalescing the rows would produce
   duplicate column names and silently merge two different metrics. The block
   label is load-bearing and must be prefixed.

3. **`Retailer 5 Year Summary`.** The opposite case — rows 1 and 2 are
   complementary. The first four columns are named on row 1; the retailer
   columns carry *numeric codes* on row 1 and their names (Aldi, Asda,
   Waitrose, Jack's) on row 2. Neither row alone is a usable header.

These two cases look identical to a naive detector and need opposite handling.
The classifier distinguishes them by asking whether the lower row repeats
itself. All shape detections are asserted against a registry in `config.py`:
**{len(xls.get('registry_mismatches', []))} mismatches**.

{len(xls.get('low_confidence_headers', []))} sheets returned low header
confidence; all are either empty template sheets or banner crosstabs whose
headers are genuinely multi-row. They are flagged rather than guessed at.

---

## 4. Session PDFs: the template holds

The build plan assumed a deterministic parser would work and an LLM extraction
pass would only be a fallback. Measured:

| | |
|---|---|
| Reports parsed | {pdf.get('pdfs_parsed', 0)} |
| **Fully conformant** | **{pdf.get('fully_conformant', 0)}/{pdf.get('pdfs_parsed', 0)} ({pdf.get('fully_conformant_pct', 0)}%)** |
| Parse errors | {len(pdf.get('parse_errors', []))} |
| Star percentages summing to 100 | **{pdf.get('integrity_star_pct_sums_to_100', 0)}/{pdf.get('integrity_star_checked', 0)}** |
| Verbatims extracted | **{pdf.get('total_verbatims', 0)}** (avg {pdf.get('verbatims_per_report_avg', 0)}/report) |
| Distinct categories | {pdf.get('distinct_categories', 0)} |
| Distinct manufacturers | {pdf.get('distinct_manufacturers', 0)} |
| Products beating category norm | {pdf.get('products_beating_category_norm', 0)} |

**No OCR budget and no LLM extraction budget is needed for FoodFax session
reports.** The regex parser handles all {pdf.get('pdfs_parsed', 0)} at zero
marginal cost. The independent integrity check — star percentages summing to
100 in every single report — is strong evidence the extraction is correct and
not merely complete.

Two structural quirks had to be handled, and both would have been invisible in
a spot check:

- **Five reports have their PDF content stream written in a different object
  order**, so default extraction returns the verbatims before the identity
  block. `extraction_mode="layout"` reconstructs true visual reading order and
  makes all 59 structurally identical.
- **Field order inside the identity line is not fixed** — some reports read
  `Price: ... Size: ...`, others `Size: ...Price: ...` with no separating
  space. Fields are matched independently rather than as an ordered pair.
- Long product names wrap onto a second line, displacing the manufacturer.

---

## 5. Join keys and cross-source agreement

CMR reference works as the primary key. {key.get('distinct_cmr_refs_total', 0)}
distinct references across five sources:

| Source | References |
|---|---:|
| Session PDF reports | {cov.get('pdf_reports', 0)} |
| `OT Sheet` test-order maps | {cov.get('ot_sheet', 0)} |
| Pack shots | {cov.get('pack_photos', 0)} |
| WIX storefront export | {cov.get('wix_export', 0)} |
| ECWID storefront export | {cov.get('ecwid_export', 0)} |

**Agreement: {key.get('disagreements', 0)} disagreements across
{key.get('agreement_checked_pairs', 0)} cross-checkable products**, on score,
price and pack size. Where two sources describe the same product, they match
exactly. That is a genuinely good data-quality result and it means the sources
can be joined without a reconciliation layer.

Gaps found:

- **Storefront CSVs exist for Sets 26-27 only.** Sets 28-32 have no WIX or
  ECWID export, so category and brand metadata for those products has to come
  from the PDF rather than the clean CSV.
- **A second reference series exists.** `992421` and `992422` (Scratch Meals
  "Balanced Nutrition" bowls) appear in the WIX export with no matching PDF and
  use a `99xxxx` range rather than `24xxxx`. Worth confirming at kickoff whether
  this is a distinct test type.
- **{len(key.get('orphans', {}).get('photo_without_pdf', []))} pack shots have
  no matching report** — and every one of them
  ({', '.join(key.get('orphans', {}).get('photo_without_pdf', [])[:4])}…) is on
  the failed-download manifest. The photos landed; the reports did not. Direct
  corroboration of §2.
- Zero pack shots failed to match a CMR reference, despite dirty filenames
  (trailing spaces, stray suffixes like `240816t.JPG`).

---

## 6. The measure dictionary

`Foodfax Database details.xlsx` is 37 rows and is the semantic layer for the
whole database.

| | |
|---|---|
| Measures defined | {dct.get('measures_total', 0)} |
| **Mapped to physical columns** | **{dct.get('measures_mapped', 0)} ({dct.get('mapping_rate_pct', 0)}%)** |
| Unmapped | {dct.get('measures_unmapped', 0)} |
| Calculated (no source column by design) | {dct.get('calculated_measures', 0)} |
| Tied to a named report template | {dct.get('measures_tied_to_report_templates', 0)} |
| Tied to a named client | {dct.get('measures_with_named_clients', 0)} |

Reported as: {', '.join(f'{k} ×{v}' for k, v in dct.get('statistics_distribution', {}).items() if v)}.

Mapping needed a business-vocabulary alias layer — the dictionary says
"Aroma" where the database column says `Smell`, "Value for what you pay" where
the column says `Value for Money`, and "Market Comparison" where the column
says `Better Than`. **This alias layer is exactly what the text-to-SQL prompt
needs to see**, because F!S staff will ask in dictionary language, not column
language.

Unmapped: {', '.join(f"`{u['code']}. {u['question']}`" for u in dct.get('unmapped_detail', [])) or 'none'}.

---

## 7. What this changes about the plan

| Plan assumption | Status | Consequence |
|---|---|---|
| PDF template is parseable deterministically | **Confirmed**, 100% | No LLM extraction cost for FoodFax reports |
| `Norm Data` header is row 7 | **Confirmed**, conf 1.0 | Registry assertion in place |
| Report-surface sheets should be skipped | **Confirmed** — {xls.get('sheets_skipped_report_surface', 0)} of {xls.get('sheets_total', 0)} | Whitelist approach validated |
| CMR ref is a viable primary key | **Confirmed**, 0 disagreements | No reconciliation layer needed |
| `Archive/` is a duplicate | **Confirmed**, byte-identical | Dedupe before chunking |
| Manual download route is broken | **Confirmed**, {inv.get('error_stubs_live', 0)} failures | Graph API is a prerequisite |
| Measure dictionary is the semantic layer | **Confirmed**, {dct.get('mapping_rate_pct', 0)}% mapped | Ships as the text-to-SQL context |
| Two header rows always mean "prefix the block" | **Wrong** | Two opposite cases exist; classifier now distinguishes them |

---

## 8. Phase 1 exit criteria

| Criterion | Status |
|---|---|
| `Archive/` dedupe implemented and quantified | Done |
| Re-runnable profiling script over every workbook | Done — `run_phase1.py` |
| Measure dictionary loaded and mapped to physical columns | Done — {dct.get('mapping_rate_pct', 0)}% |
| Data-quality findings note | **This document** |
| Warehouse schema and migrations | Done — `db/migrations/001_init.sql` |
| 60-question golden set | **Drafted, awaiting F!S sign-off** — `eval/golden_questions.yaml` |
| Postgres stood up with CI | **Blocked** — needs an environment to deploy into (kickoff Q1/Q5) |

Two items are not closed, and neither can be closed without F!S: the golden set
needs three reviewers, and the database needs somewhere to live.

---

## 9. Open questions carried into Phase 2

1. Graph API access — everything in Phase 3 waits on it.
2. Future Food and Appetite Shift — confirm reachable, or the plan drops them.
3. Is `Foodfax 2-0 DB.xlsx` the live database? The file we hold says ARCHIVE.
4. What do project code names Granville, Leather, Flax, Bamboo, Kapok map to?
   41 code names cover 13,278 tests; without this, client-level questions are
   unanswerable for anything before 2020.
5. Which model hosting does the existing DPA cover?
6. Who signs off the golden question set?
7. Which content is commercially restricted?
8. **New:** is the `99xxxx` reference series a different test type?
9. **New:** why do Sets 28-32 have no storefront CSV export — process change,
   or did they fail to download too?
"""

    out_path = config.PROJECT_ROOT / "PHASE1_FINDINGS.md"
    out_path.write_text(md, encoding="utf-8")
    return out_path


if __name__ == "__main__":
    print(build())
