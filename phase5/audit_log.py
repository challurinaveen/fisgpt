"""
Audit log — persistent record of every query and answer.

Stores: timestamp, user question, SQL executed, chunks retrieved,
answer text, model version, token count, elapsed time.

Queryable via the Data Explorer or directly in DuckDB.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import duckdb


_DB_PATH = Path(__file__).resolve().parent.parent / "out" / "fis_audit.duckdb"


def _connect():
    """Get a connection to the audit database (creates it if needed)."""
    con = duckdb.connect(str(_DB_PATH))
    con.execute("""
        CREATE TABLE IF NOT EXISTS audit_log (
            id          INTEGER PRIMARY KEY DEFAULT nextval('audit_seq'),
            ts          TIMESTAMP NOT NULL,
            question    TEXT NOT NULL,
            answer      TEXT,
            tool_calls  TEXT,
            model       TEXT,
            provider    TEXT,
            tokens      INTEGER,
            rounds      INTEGER,
            elapsed_s   REAL
        )
    """)
    # Create sequence if it doesn't exist
    try:
        con.execute("CREATE SEQUENCE IF NOT EXISTS audit_seq START 1")
    except Exception:
        pass
    return con


def log_query(
    question: str,
    answer: str,
    tool_calls: list | None = None,
    model: str = "",
    provider: str = "",
    tokens: int = 0,
    rounds: int = 0,
    elapsed_s: float = 0.0,
) -> None:
    """Write one query+answer to the audit log."""
    try:
        con = _connect()
        con.execute(
            """
            INSERT INTO audit_log (ts, question, answer, tool_calls, model, provider, tokens, rounds, elapsed_s)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                datetime.now(timezone.utc),
                question,
                answer,
                json.dumps(tool_calls) if tool_calls else None,
                model,
                provider,
                tokens,
                rounds,
                round(elapsed_s, 2),
            ],
        )
        con.close()
    except Exception:
        # Never let logging failures break the app
        pass


def get_recent(limit: int = 50) -> list[dict]:
    """Fetch the most recent audit log entries."""
    try:
        con = _connect()
        rows = con.execute(
            """
            SELECT ts, question, answer, model, tokens, rounds, elapsed_s
            FROM audit_log
            ORDER BY ts DESC
            LIMIT ?
            """,
            [limit],
        ).fetchall()
        con.close()
        return [
            {
                "timestamp": str(r[0]),
                "question": r[1],
                "answer": r[2][:200] + "…" if r[2] and len(r[2]) > 200 else r[2],
                "model": r[3],
                "tokens": r[4],
                "rounds": r[5],
                "elapsed_s": r[6],
            }
            for r in rows
        ]
    except Exception:
        return []


def get_stats() -> dict:
    """Get aggregate audit stats."""
    try:
        con = _connect()
        row = con.execute("""
            SELECT
                count(*) AS total_queries,
                coalesce(sum(tokens), 0) AS total_tokens,
                round(avg(elapsed_s), 1) AS avg_elapsed,
                min(ts) AS first_query,
                max(ts) AS last_query
            FROM audit_log
        """).fetchone()
        con.close()
        return {
            "total_queries": row[0],
            "total_tokens": row[1],
            "avg_elapsed": row[2],
            "first_query": str(row[3]) if row[3] else None,
            "last_query": str(row[4]) if row[4] else None,
        }
    except Exception:
        return {}
