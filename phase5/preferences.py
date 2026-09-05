"""
User preference memory — the chatbot learns how users want answers.

Stores preferences in a DuckDB table.  Each preference is a short
instruction the user has given (explicitly or via feedback), e.g.:

  "Always show data as tables, not paragraphs"
  "Include base sizes prominently"
  "Compare products to category norms when possible"
  "When I ask about a category, also show the top manufacturers"

On every new chat turn the active preferences are loaded and injected
into the system prompt so the LLM follows them automatically.

The LLM can also detect implicit preferences from conversation
patterns and suggest saving them.
"""
from __future__ import annotations

import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import duckdb


# ── database path ────────────────────────────────────────────────────

def _db_path() -> str:
    """Return the preferences database path (writable)."""
    code_dir = Path(__file__).resolve().parent.parent
    out_dir = code_dir / "out"
    if out_dir.exists():
        return str(out_dir / "fis_preferences.duckdb")
    return str(Path(tempfile.gettempdir()) / "fis_preferences.duckdb")


def _connect():
    """Get a connection (creates table if needed)."""
    con = duckdb.connect(_db_path())
    con.execute("""
        CREATE TABLE IF NOT EXISTS preferences (
            id       INTEGER PRIMARY KEY DEFAULT nextval('pref_seq'),
            text     TEXT NOT NULL,
            active   BOOLEAN NOT NULL DEFAULT TRUE,
            created  TIMESTAMP NOT NULL
        )
    """)
    return con


def _ensure_seq():
    """Make sure the sequence exists before first insert."""
    try:
        con = duckdb.connect(_db_path())
        con.execute("CREATE SEQUENCE IF NOT EXISTS pref_seq START 1")
        con.execute("""
            CREATE TABLE IF NOT EXISTS preferences (
                id       INTEGER PRIMARY KEY DEFAULT nextval('pref_seq'),
                text     TEXT NOT NULL,
                active   BOOLEAN NOT NULL DEFAULT TRUE,
                created  TIMESTAMP NOT NULL
            )
        """)
        con.close()
    except Exception as e:
        print(f"[preferences] init failed: {e}", file=sys.stderr)


# Run on import so the table exists
_ensure_seq()


# ── public API ───────────────────────────────────────────────────────

def add_preference(text: str) -> None:
    """Save a new preference."""
    try:
        con = _connect()
        con.execute(
            "INSERT INTO preferences (text, active, created) VALUES (?, TRUE, ?)",
            [text.strip(), datetime.now(timezone.utc)],
        )
        con.close()
    except Exception as e:
        print(f"[preferences] write failed: {e}", file=sys.stderr)


def get_active_preferences() -> list[str]:
    """Return all active preference strings."""
    try:
        con = _connect()
        rows = con.execute(
            "SELECT text FROM preferences WHERE active = TRUE ORDER BY created"
        ).fetchall()
        con.close()
        return [r[0] for r in rows]
    except Exception:
        return []


def get_all_preferences() -> list[dict]:
    """Return all preferences with metadata."""
    try:
        con = _connect()
        rows = con.execute(
            "SELECT id, text, active, created FROM preferences ORDER BY created DESC"
        ).fetchall()
        con.close()
        return [
            {"id": r[0], "text": r[1], "active": r[2], "created": str(r[3])}
            for r in rows
        ]
    except Exception:
        return []


def toggle_preference(pref_id: int, active: bool) -> None:
    """Enable or disable a preference."""
    try:
        con = _connect()
        con.execute(
            "UPDATE preferences SET active = ? WHERE id = ?",
            [active, pref_id],
        )
        con.close()
    except Exception as e:
        print(f"[preferences] toggle failed: {e}", file=sys.stderr)


def delete_preference(pref_id: int) -> None:
    """Permanently remove a preference."""
    try:
        con = _connect()
        con.execute("DELETE FROM preferences WHERE id = ?", [pref_id])
        con.close()
    except Exception as e:
        print(f"[preferences] delete failed: {e}", file=sys.stderr)


def build_preference_prompt() -> str:
    """
    Build a prompt fragment from active preferences.

    This gets appended to the system prompt before each LLM call.
    """
    prefs = get_active_preferences()
    if not prefs:
        return ""

    lines = [
        "",
        "═══════════════════════════════════════════════════════════════",
        "USER PREFERENCES (learned from past interactions — follow these)",
        "═══════════════════════════════════════════════════════════════",
        "",
    ]
    for i, p in enumerate(prefs, 1):
        lines.append(f"  {i}. {p}")

    lines.append("")
    lines.append("Apply these preferences to every answer unless the user")
    lines.append("explicitly asks for something different.")
    return "\n".join(lines)
