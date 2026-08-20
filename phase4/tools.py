"""
Phase 4 · Tool execution layer.

Implements the two tools Claude can call:
  run_sql      — execute read-only SQL against the DuckDB warehouse
  search_docs  — search the document chunk index for relevant passages

Both return plain-text results formatted for LLM consumption.
"""
from __future__ import annotations

import json
import traceback
from pathlib import Path

import duckdb

import config
from phase2 import warehouse
from phase3 import search as chunk_search


# ── SQL tool ───────────────────────────────────────────────────────────

_ALLOWED_SCHEMAS = {"curated", "ffx", "docs"}
_MAX_ROWS = 100


def execute_sql(sql: str) -> str:
    """
    Execute a read-only SQL query and return results as a markdown table.

    Safety:
      - Only SELECT statements allowed
      - Only curated.*, ffx.*, docs.* schemas
      - Max 100 rows returned
    """
    sql_stripped = sql.strip().rstrip(";")

    # Basic safety check — only SELECT
    first_word = sql_stripped.split()[0].upper() if sql_stripped else ""
    if first_word not in ("SELECT", "WITH", "EXPLAIN"):
        return "ERROR: Only SELECT queries are allowed."

    # Block obvious mutations
    sql_upper = sql_stripped.upper()
    for forbidden in ("INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE",
                      "TRUNCATE", "GRANT", "REVOKE"):
        # Check it's a keyword, not part of a column name
        if f" {forbidden} " in f" {sql_upper} ":
            return f"ERROR: {forbidden} statements are not allowed."

    try:
        con = warehouse.connect(read_only=True)

        # Execute with row limit
        limited_sql = sql_stripped
        if "LIMIT" not in sql_upper:
            limited_sql += f" LIMIT {_MAX_ROWS}"

        result = con.execute(limited_sql)
        columns = [desc[0] for desc in result.description]
        rows = result.fetchall()
        con.close()

        if not rows:
            return f"Query returned 0 rows.\n\nSQL: {sql_stripped}"

        # Format as markdown table
        lines = []
        lines.append("| " + " | ".join(columns) + " |")
        lines.append("| " + " | ".join(["---"] * len(columns)) + " |")

        for row in rows:
            cells = []
            for v in row:
                if v is None:
                    cells.append("")
                elif isinstance(v, float):
                    # Smart formatting: integers show as int, floats get 2-4 decimals
                    if v == int(v) and abs(v) < 1e6:
                        cells.append(f"{int(v):,}")
                    elif abs(v) < 0.01:
                        cells.append(f"{v:.4f}")
                    else:
                        cells.append(f"{v:,.2f}")
                elif isinstance(v, bool):
                    cells.append("Yes" if v else "No")
                elif isinstance(v, int):
                    cells.append(f"{v:,}")
                else:
                    # Truncate long text
                    s = str(v)
                    if len(s) > 120:
                        s = s[:117] + "..."
                    cells.append(s)
            lines.append("| " + " | ".join(cells) + " |")

        result_text = "\n".join(lines)

        footer = f"\n\n{len(rows)} row{'s' if len(rows) != 1 else ''} returned."
        if len(rows) >= _MAX_ROWS:
            footer += " (truncated at 100 — add a LIMIT or WHERE to narrow)."

        return result_text + footer

    except duckdb.Error as e:
        return f"SQL ERROR: {e}\n\nSQL: {sql_stripped}"
    except Exception as e:
        return f"ERROR: {e}"


# ── Document search tool ──────────────────────────────────────────────

def execute_search(query: str, top_k: int = 5, source_type: str | None = None) -> str:
    """
    Search the chunk index and return formatted results with citations.

    When no source_type filter is set, runs a diversified search that ensures
    different chunk types appear in the results (not just the top-scoring type).
    """
    import os
    top_k = min(max(1, top_k), 10)

    source_types = [source_type] if source_type else None

    try:
        if source_types:
            # Filtered search — single type
            results = chunk_search.search(
                query=query,
                top_k=top_k,
                source_types=source_types,
            )
        else:
            # Diversified search — get results from each type and interleave
            all_results = chunk_search.search(query=query, top_k=top_k * 2)

            # Also explicitly search verbatim_group and methodology/crosstab
            # to avoid TF-IDF bias toward product_report chunks
            extra_types = ["verbatim_group", "methodology", "crosstab_summary"]
            for etype in extra_types:
                extra = chunk_search.search(
                    query=query, top_k=3, source_types=[etype],
                )
                all_results.extend(extra)

            # Deduplicate by chunk_id
            seen = set()
            deduped = []
            for r in all_results:
                if r.chunk_id not in seen:
                    seen.add(r.chunk_id)
                    deduped.append(r)

            # Sort by score, take top_k
            deduped.sort(key=lambda r: r.score, reverse=True)
            results = deduped[:top_k]

        if not results:
            return "No relevant documents found for this query."

        parts = []
        for i, r in enumerate(results, 1):
            header = f"[{i}] {r.locator}"
            source_filename = os.path.basename(r.source_file) if r.source_file else ""
            meta = f"Source file: {source_filename} | Type: {r.source_type}"
            if r.cmr_ref:
                meta += f" | CMR: {r.cmr_ref}"
            if r.category_name:
                meta += f" | Category: {r.category_name}"
            cite_hint = f"CITE AS: {source_filename}" if source_filename else ""
            parts.append(f"{header}\n{meta}\n{cite_hint}\nScore: {r.score:.3f}\n\n{r.text}")

        return "\n\n" + ("─" * 40) + "\n\n".join(parts)

    except Exception as e:
        return f"SEARCH ERROR: {e}\n{traceback.format_exc()}"


# ── Tool dispatcher ───────────────────────────────────────────────────

def dispatch_tool(tool_name: str, tool_input: dict) -> str:
    """
    Route a tool call to the appropriate executor.

    Returns the tool result as a string.
    """
    if tool_name == "run_sql":
        sql = tool_input.get("sql", "")
        return execute_sql(sql)
    elif tool_name == "search_docs":
        query = tool_input.get("query", "")
        top_k = tool_input.get("top_k", 5)
        source_type = tool_input.get("source_type")
        return execute_search(query, top_k, source_type)
    else:
        return f"Unknown tool: {tool_name}"
