"""
Data refresh script — rebuild the warehouse when new data arrives.

    python fis-gpt/run_refresh.py [--skip-phase1] [--skip-embeddings]

Runs Phases 1 → 2 → 3 in sequence to reparse every source and rebuild
the DuckDB warehouse + chunk index.  Phase 4 (LLM layer) is stateless
and doesn't need a rebuild step.

Flags:
  --skip-phase1      Skip the profiling/audit pass (saves ~30 s if no
                     new files were added to the corpus folder)
  --skip-embeddings  Reuse existing embeddings (saves ~60 s if the only
                     changes are new products, not new document types)

Exit codes:
  0  everything passed
  1  at least one phase failed (check the console output)
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")


def main() -> int:
    skip_p1 = "--skip-phase1" in sys.argv
    skip_embed = "--skip-embeddings" in sys.argv

    print("=" * 62)
    print("  F!S Internal GPT — Full Data Refresh")
    print("=" * 62)
    t0 = time.time()

    # ── Phase 1: Profile & audit ────────────────────────────────────
    if skip_p1:
        print("\n[Phase 1] skipped (--skip-phase1)")
    else:
        print("\n[Phase 1] Profiling corpus…")
        import run_phase1
        rc = run_phase1.main()
        if rc != 0:
            print("Phase 1 failed — aborting.")
            return 1

    # ── Phase 2: Warehouse build ────────────────────────────────────
    print("\n[Phase 2] Building warehouse…")
    import run_phase2
    rc = run_phase2.main()
    if rc != 0:
        print("Phase 2 failed — aborting.")
        return 1

    # ── Phase 3: Chunks & embeddings ────────────────────────────────
    if skip_embed:
        print("\n[Phase 3] skipped (--skip-embeddings)")
    else:
        print("\n[Phase 3] Building chunks & embeddings…")
        import run_phase3
        rc = run_phase3.main()
        if rc != 0:
            print("Phase 3 failed — aborting.")
            return 1

    # ── Summary ─────────────────────────────────────────────────────
    elapsed = time.time() - t0
    print("\n" + "=" * 62)
    print(f"  Refresh complete in {elapsed:.0f}s")
    print("  Restart the Streamlit server to pick up changes.")
    print("=" * 62)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
