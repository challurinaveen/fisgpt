"""
Phase 3 · Build the unstructured (RAG) pipeline.

    python fis-gpt/run_phase3.py

Chunks every document source into retrieval-ready text blocks, generates
vector embeddings, stores them on disk, loads them into DuckDB, and
verifies that the search pipeline returns sensible results for the
document-lookup golden questions.

Prerequisites:
  - run_phase1.py  (needs pdf_records.json, measure_dictionary.json)
  - run_phase2.py  (needs fis_warehouse.duckdb)
  - pip install sentence-transformers   (or scikit-learn for fallback)
  - pip install numpy

Outputs:
  - fis-gpt/out/chunks.json          (full chunk texts + metadata)
  - fis-gpt/out/chunk_index.json     (lightweight index for search)
  - fis-gpt/out/chunk_embeddings.npy (dense vectors)
  - docs.chunk table added to fis_warehouse.duckdb
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

import numpy as np

import config
from phase2 import warehouse
from phase3 import chunker, embeddings, search


def main() -> int:
    config.ensure_out()

    print(f"\nF!S internal GPT — Phase 3 (unstructured pipeline)")
    print(f"corpus: {config.CORPUS_ROOT}")
    print(f"warehouse: {warehouse.DB_PATH}\n")

    t_total = time.time()
    failed = []

    # ── 0. Connect to warehouse ────────────────────────────────────────
    con = warehouse.connect()

    # ── 1. Build chunks ────────────────────────────────────────────────
    print("[chunking]")
    t0 = time.time()
    try:
        chunks = chunker.build_all_chunks(con)

        # Save full chunks to JSON
        chunks_path = config.OUT_DIR / "chunks.json"
        chunks_path.write_text(
            json.dumps(chunker.chunks_to_dicts(chunks), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"  total: {len(chunks)} chunks")
        print(f"  saved {chunks_path.name}")
        print(f"  done in {time.time() - t0:.1f}s\n")
    except Exception:
        failed.append("chunking")
        traceback.print_exc()
        print(f"  FAILED after {time.time() - t0:.1f}s\n")
        chunks = []

    if not chunks:
        print("No chunks built — cannot continue.")
        con.close()
        return 1

    # ── 2. Generate embeddings ─────────────────────────────────────────
    print("[embeddings]")
    t0 = time.time()
    embed_array = None
    backend = None
    try:
        embed_array, backend = embeddings.embed_chunks(chunks)
        embeddings.save_embeddings(chunks, embed_array, backend)
        print(f"  done in {time.time() - t0:.1f}s\n")
    except Exception:
        failed.append("embeddings")
        traceback.print_exc()
        print(f"  FAILED after {time.time() - t0:.1f}s\n")

    # ── 3. Load chunks into DuckDB ─────────────────────────────────────
    print("[DuckDB chunk table]")
    t0 = time.time()
    try:
        _create_docs_schema(con)
        _load_chunks_to_db(con, chunks, embed_array)
        n_loaded = con.execute("SELECT count(*) FROM docs.chunk").fetchone()[0]
        print(f"  {n_loaded} chunks loaded into docs.chunk")
        print(f"  done in {time.time() - t0:.1f}s\n")
    except Exception:
        failed.append("DuckDB chunk load")
        traceback.print_exc()
        print(f"  FAILED after {time.time() - t0:.1f}s\n")

    # ── 4. Verify retrieval ────────────────────────────────────────────
    print("[retrieval verification]")
    verify_results = _verify_retrieval(chunks, backend)

    # Save results
    (config.OUT_DIR / "phase3_verify.json").write_text(
        json.dumps(verify_results, indent=2, default=str), encoding="utf-8"
    )

    # ── Dashboard ──────────────────────────────────────────────────────
    summary = verify_results.get("_summary", {})
    chunk_types = {}
    for c in chunks:
        chunk_types[c.source_type] = chunk_types.get(c.source_type, 0) + 1

    print("\n" + "─" * 62)
    for stype, count in sorted(chunk_types.items()):
        print(f"  {stype:25s} {count:>6} chunks")
    print(f"  {'total':25s} {len(chunks):>6} chunks")
    if embed_array is not None:
        print(f"  {'embedding dims':25s} {embed_array.shape[1]:>6}")
        print(f"  {'embedding backend':25s} {backend:>6}")
    print("─" * 62)
    print(f"\n  verification: {summary.get('passed', 0)}/{summary.get('total', 0)} passed")
    print(f"  chunks:       {config.OUT_DIR / 'chunks.json'}")
    print(f"  embeddings:   {config.OUT_DIR / 'chunk_embeddings.npy'}")
    print(f"  total time:   {time.time() - t_total:.1f}s\n")

    con.close()

    if failed:
        print(f"STAGES FAILED: {', '.join(failed)}")
        return 1
    if summary.get("failed", 0) > 0:
        print(f"VERIFICATION FAILURES: {summary['failed']}")
        return 1
    return 0


# ── helpers ────────────────────────────────────────────────────────────

def _create_docs_schema(con) -> None:
    """Create the docs schema and chunk table in DuckDB."""
    con.execute("CREATE SCHEMA IF NOT EXISTS docs")

    con.execute("DROP TABLE IF EXISTS docs.chunk")
    con.execute("""
    CREATE TABLE docs.chunk (
        chunk_id       INTEGER PRIMARY KEY,
        source_type    TEXT NOT NULL,
        source_file    TEXT,
        cmr_ref        TEXT,
        category_name  TEXT,
        test_year      INTEGER,
        locator        TEXT NOT NULL,
        text           TEXT NOT NULL,
        embed_dim      INTEGER,
        embedding_blob BLOB
    )
    """)


def _load_chunks_to_db(con, chunks, embed_array) -> None:
    """Insert chunks (and optionally embeddings as BLOBs) into DuckDB."""
    import pandas as pd

    rows = []
    for i, c in enumerate(chunks):
        row = {
            "chunk_id": c.chunk_id,
            "source_type": c.source_type,
            "source_file": c.source_file,
            "cmr_ref": c.cmr_ref,
            "category_name": c.category_name,
            "test_year": c.test_year,
            "locator": c.locator,
            "text": c.text,
            "embed_dim": int(embed_array.shape[1]) if embed_array is not None else None,
            "embedding_blob": embed_array[i].tobytes() if embed_array is not None else None,
        }
        rows.append(row)

    df = pd.DataFrame(rows)
    con.execute("INSERT INTO docs.chunk SELECT * FROM df")


def _verify_retrieval(chunks, backend) -> dict:
    """
    Run a set of document-lookup test queries and check that the
    top results are relevant.

    Each test specifies a query, expected source_type, and a substring
    that must appear in the top-k results' text.
    """
    tests = [
        {
            "name": "D01: Verbatims for Red Leicester Bites",
            "query": "What did reviewers say about the texture of the Red Leicester Bites?",
            "expect_source_type": "verbatim_group",
            "expect_substring": "Red Leicester",
            "top_k": 3,
        },
        {
            "name": "D02: Product report for a session product",
            "query": "What score did the Jacob's Red Leicester Bites get?",
            "expect_source_type": "product_report",
            "expect_substring": "Red Leicester",
            "top_k": 3,
        },
        {
            "name": "D03: Measure definition lookup",
            "query": "What does the FoodFax questionnaire measure for Taste?",
            "expect_source_type": "measure_def",
            "expect_substring": "Taste",
            "top_k": 5,
        },
        {
            "name": "D04: Category norm lookup",
            "query": "How many savoury biscuits have been tested?",
            "expect_source_type": "category_norm",
            "expect_substring": "BISCUIT",
            "top_k": 5,
        },
        {
            "name": "D05: Verbatim sentiment",
            "query": "Show me negative reviews with 1 or 2 stars",
            "expect_source_type": "verbatim_group",
            "expect_substring": "★★",
            "top_k": 5,
        },
        {
            "name": "D06: Measure definition for Purchase Intent",
            "query": "How is Purchase Intent measured in FoodFax?",
            "expect_source_type": "measure_def",
            "expect_substring": "Purchase",
            "top_k": 5,
        },
    ]

    results = {"tests": [], "_summary": {"total": len(tests), "passed": 0, "failed": 0}}

    for test in tests:
        name = test["name"]
        try:
            hits = search.search(
                test["query"],
                top_k=test["top_k"],
            )

            # Check: at least one hit with the expected source_type
            type_match = any(h.source_type == test["expect_source_type"] for h in hits)

            # Check: at least one hit contains the expected substring
            substr = test["expect_substring"].lower()
            text_match = any(substr in h.text.lower() for h in hits)

            passed = type_match and text_match
            status = "PASS" if passed else "FAIL"

            if passed:
                results["_summary"]["passed"] += 1
            else:
                results["_summary"]["failed"] += 1

            results["tests"].append({
                "name": name,
                "status": status,
                "type_match": type_match,
                "text_match": text_match,
                "n_hits": len(hits),
                "top_hit": hits[0].locator if hits else None,
                "top_score": hits[0].score if hits else None,
            })

            hit_summary = f"top={hits[0].locator}" if hits else "no hits"
            print(f"  {status:4s}  {name:50s}  {hit_summary}")

        except Exception as e:
            results["_summary"]["failed"] += 1
            results["tests"].append({
                "name": name,
                "status": "ERROR",
                "error": str(e),
            })
            print(f"  ERR   {name:50s}  {e}")

    return results


if __name__ == "__main__":
    raise SystemExit(main())
