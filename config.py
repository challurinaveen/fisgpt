"""
Central configuration: where the corpus lives, where output goes, and what we
already know about specific files.

Everything downstream imports paths from here so that pointing the pipeline at
a SharePoint-synced copy later means changing one constant, not twenty.
"""
from pathlib import Path

# --------------------------------------------------------------------------
# Roots
# --------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
CORPUS_ROOT = PROJECT_ROOT / "Fis Group"
OUT_DIR = PROJECT_ROOT / "fis-gpt" / "out"

# The live copy. `Archive/` is a byte-identical duplicate (Finding D) and is
# excluded from ingest, but still walked by the inventory so we can *prove*
# it is a duplicate rather than assume it.
LIVE_DATA = CORPUS_ROOT / "fis group data"
ARCHIVE_DATA = CORPUS_ROOT / "Archive"
PROJECT_DOCS = CORPUS_ROOT / "FIS Project Documents"

FOODFAX = LIVE_DATA / "Project Data" / "FoodFax"
FFX_EXCEL = FOODFAX / "Excel Docs"
FFX_INFO = FOODFAX / "Info"
FFX_REPORTS = FOODFAX / "PDF Reports"

# --------------------------------------------------------------------------
# Walk rules
# --------------------------------------------------------------------------
SKIP_DIR_NAMES = {"__MACOSX", ".git", "__pycache__", "chroma_db", "out"}
SKIP_FILE_NAMES = {".DS_Store", "Thumbs.db"}

EXT_CLASS = {
    ".pdf": "doc_pdf",
    ".docx": "doc_docx",
    ".pptx": "doc_pptx",
    ".xlsx": "sheet_xlsx",
    ".xlsm": "sheet_xlsm",
    ".xls": "sheet_xls",
    ".csv": "data_csv",
    ".jpg": "image", ".jpeg": "image", ".png": "image", ".gif": "image",
    ".mp4": "video", ".mov": "video",
    ".txt": "text",
    ".py": "code", ".json": "code", ".html": "code", ".md": "code",
}

# --------------------------------------------------------------------------
# Known-shape registry.
#
# Populated from the audit in the build plan. The profiler runs blind and then
# checks itself against this, so a silent regression in header detection shows
# up as a mismatch rather than as quietly corrupted data (§04, "the rule that
# prevents the worst failure mode").
# --------------------------------------------------------------------------
KNOWN_SHAPES = {
    ("Foodfax Database ARCHIVE.xlsm", "Norm Data"):
        {"shape": "stats_block", "header_row": 7},
    ("Foodfax Database ARCHIVE.xlsm", "Historic Products"):
        {"shape": "flat", "header_row": 1},
    ("Foodfax Database ARCHIVE.xlsm", "Current Norm Summary"):
        {"shape": "flat", "header_row": 1},
    ("Foodfax Database ARCHIVE.xlsm", "Current Retailer Summary"):
        {"shape": "flat", "header_row": 1},
    # Rows 1 and 2 are complementary, not stacked: the first four columns are
    # named on row 1, the retailer columns carry numeric codes on row 1 and
    # their names on row 2. Neither row alone is a usable header.
    ("Foodfax Database ARCHIVE.xlsm", "Retailer 5 Year Summary"):
        {"shape": "coalesce_header", "header_row": 1},
    ("FFX DB Update pre 2021.xlsx", "Sheet1"):
        {"shape": "two_row_header", "header_row": 1},
    ("Foodfax Database details.xlsx", "Sheet1"):
        {"shape": "flat", "header_row": 1},
}

# Sheets worth ingesting from the 44-sheet session workbooks (§04).
# Everything else in those files is a report-rendering surface, not data.
SESSION_WORKBOOK_WHITELIST = {
    "OT Sheet", "FFX DATA", "Verbatims", "Tables",
    "Wix Short", "ECWID Store Export", "Awards Summary", "References",
}
# Sheets we deliberately skip, matched as prefixes.
REPORT_SURFACE_PREFIXES = ("Short ", "Full ", "BMPair", "BMTrio")

# --------------------------------------------------------------------------
# Join key
# --------------------------------------------------------------------------
# CMR reference is the natural primary key across PDFs, crosstabs, photos and
# storefront exports. Six digits, occasionally with stray whitespace or a
# trailing letter in filenames (e.g. "240816t.JPG").
CMR_REF_PATTERN = r"\b(\d{6})\b"


def ensure_out() -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    return OUT_DIR
