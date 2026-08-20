"""
Phase 1 · run everything.

    python fis-gpt/run_phase1.py

Order matters: the measure dictionary maps onto columns discovered by the
workbook profiler, and the cross-source check reads the parsed PDF records.
Each stage writes its artefacts to fis-gpt/out/ and the last stage builds the
findings note from them, so the note can never disagree with the numbers.
"""
from __future__ import annotations

import json
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# Windows consoles default to cp1252, which cannot encode the characters used
# in the corpus (or in this script's own output). Force UTF-8 so a run does not
# die on a print after every stage has already succeeded.
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

import config                                    # noqa: E402
from phase1 import (                             # noqa: E402
    inventory, profile_excel, profile_pdf, crosscheck, dictionary, report,
)

STAGES = [
    ("inventory + dedupe", inventory.run),
    ("workbook profiling", profile_excel.run),
    ("session PDF parsing", profile_pdf.run),
    ("join-key cross-check", crosscheck.run),
    ("measure dictionary", dictionary.run),
]


def main() -> int:
    config.ensure_out()
    print(f"\nF!S internal GPT — Phase 1\ncorpus: {config.CORPUS_ROOT}\n")

    results, failed = {}, []
    for name, fn in STAGES:
        print(f"[{name}]")
        t0 = time.time()
        try:
            results[name] = fn()
            print(f"  done in {time.time() - t0:.1f}s\n")
        except Exception:                                     # noqa: BLE001
            failed.append(name)
            traceback.print_exc()
            print(f"  FAILED after {time.time() - t0:.1f}s\n")

    path = report.build()

    (config.OUT_DIR / "phase1_run.json").write_text(
        json.dumps({"stages": list(results), "failed": failed,
                    "results": results}, indent=2, default=str),
        encoding="utf-8")

    print("─" * 62)
    inv = results.get("inventory + dedupe", {})
    xls = results.get("workbook profiling", {})
    pdf = results.get("session PDF parsing", {})
    key = results.get("join-key cross-check", {})
    dct = results.get("measure dictionary", {})
    print(f"  corpus            {inv.get('files_live_real', 0)} real files, "
          f"{inv.get('duplicate_files', 0)} duplicates removed from scope")
    print(f"  missing           {inv.get('missing_manifest_entries', 0)} items "
          f"across {len(inv.get('missing_by_dataset', {}))} datasets")
    print(f"  workbooks         {xls.get('sheets_to_ingest', 0)}/{xls.get('sheets_total', 0)} "
          f"sheets to ingest, {len(xls.get('registry_mismatches', []))} shape mismatches")
    print(f"  session PDFs      {pdf.get('fully_conformant', 0)}/{pdf.get('pdfs_parsed', 0)} conformant, "
          f"{pdf.get('total_verbatims', 0)} verbatims")
    print(f"  join keys         {key.get('disagreements', 0)} disagreements across "
          f"{key.get('agreement_checked_pairs', 0)} cross-checked products")
    print(f"  measures          {dct.get('measures_mapped', 0)}/{dct.get('measures_total', 0)} "
          f"mapped to physical columns")
    print("─" * 62)
    print(f"\nfindings note  -> {path}")
    print(f"artefacts      -> {config.OUT_DIR}\n")

    if failed:
        print(f"STAGES FAILED: {', '.join(failed)}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
