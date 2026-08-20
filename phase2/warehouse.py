"""
Phase 2 · DuckDB warehouse — schema, loading, connection management.

Uses DuckDB as a local analytic warehouse so we can verify every golden-set
query without standing up Postgres. The tables mirror 001_init.sql closely
enough that the SQL in golden_questions.yaml works unchanged.

The production deployment (Phase 5+) migrates these same tables to Postgres 16
+ pgvector; DuckDB is the development-time proving ground.

Output: fis-gpt/out/fis_warehouse.duckdb
"""
from __future__ import annotations

import duckdb
import pandas as pd
from pathlib import Path

import config

DB_PATH = config.OUT_DIR / "fis_warehouse.duckdb"


def connect(read_only: bool = False) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(DB_PATH), read_only=read_only)


def create_schema(con: duckdb.DuckDBPyConnection) -> None:
    """Create all warehouse tables and curated views."""

    con.execute("CREATE SCHEMA IF NOT EXISTS ffx")
    con.execute("CREATE SCHEMA IF NOT EXISTS meta")
    con.execute("CREATE SCHEMA IF NOT EXISTS curated")

    # ── product_test: the grain of the whole warehouse ──────────────────
    con.execute("""
    CREATE TABLE IF NOT EXISTS ffx.product_test (
        product_test_id   INTEGER PRIMARY KEY,
        unique_reference  TEXT,
        cmr_ref           TEXT,
        prodref           TEXT,
        project_code      TEXT,
        project_name      TEXT,
        project_type      TEXT,
        product_name      TEXT,
        category_code     TEXT,
        category_name     TEXT,
        manufacturer_code TEXT,
        manufacturer_name TEXT,
        own_label_or_brand TEXT,
        test_year         INTEGER,
        test_date         TEXT,
        session_set       TEXT,
        test_order        INTEGER,
        price_gbp         DOUBLE,
        pack_size         TEXT,
        storage           TEXT,
        sweet_or_savoury  TEXT,
        fax_type          TEXT,
        tier              TEXT,
        sample_size       INTEGER,
        attribute_scale   SMALLINT,
        source_table      TEXT,
        source_file       TEXT
    )
    """)

    # ── measure: seeded from the dictionary ─────────────────────────────
    con.execute("""
    CREATE TABLE IF NOT EXISTS ffx.measure (
        measure_id    INTEGER PRIMARY KEY,
        measure_code  TEXT UNIQUE,
        measure_name  TEXT NOT NULL,
        asked_of      TEXT,
        is_calculated BOOLEAN DEFAULT FALSE,
        definition    TEXT
    )
    """)

    # ── measure_value: melted long-form fact table ──────────────────────
    con.execute("""
    CREATE TABLE IF NOT EXISTS ffx.measure_value (
        product_test_id INTEGER NOT NULL,
        measure_id      INTEGER NOT NULL,
        variant         TEXT    NOT NULL,
        value           DOUBLE,
        source_table    TEXT,
        PRIMARY KEY (product_test_id, measure_id, variant)
    )
    """)

    # ── session_report: from one-page PDFs ──────────────────────────────
    con.execute("""
    CREATE TABLE IF NOT EXISTS ffx.session_report (
        product_test_id  INTEGER PRIMARY KEY,
        score_out_of_50  INTEGER,
        category_average DOUBLE,
        vs_category_norm DOUBLE,
        star_5_pct       INTEGER,
        star_4_pct       INTEGER,
        star_3_pct       INTEGER,
        star_2_pct       INTEGER,
        star_1_pct       INTEGER,
        award_quality    BOOLEAN,
        award_taste      BOOLEAN,
        award_value      BOOLEAN
    )
    """)

    # ── category_award ──────────────────────────────────────────────────
    con.execute("""
    CREATE TABLE IF NOT EXISTS ffx.category_award (
        product_test_id INTEGER NOT NULL,
        award_label     TEXT    NOT NULL,
        pct             INTEGER NOT NULL,
        rank            SMALLINT,
        PRIMARY KEY (product_test_id, award_label)
    )
    """)

    # ── verbatim ────────────────────────────────────────────────────────
    con.execute("""
    CREATE TABLE IF NOT EXISTS ffx.verbatim (
        verbatim_id     INTEGER PRIMARY KEY,
        product_test_id INTEGER NOT NULL,
        stars           SMALLINT,
        text            TEXT NOT NULL,
        pii_scrubbed    BOOLEAN DEFAULT TRUE,
        source          TEXT
    )
    """)

    # ── crosstab_cell ───────────────────────────────────────────────────
    con.execute("""
    CREATE TABLE IF NOT EXISTS ffx.crosstab_cell (
        crosstab_cell_id INTEGER PRIMARY KEY,
        product_test_id  INTEGER,
        cmr_ref          TEXT,
        question_text    TEXT NOT NULL,
        response_option  TEXT,
        banner_group     TEXT NOT NULL,
        banner_level     TEXT NOT NULL,
        banner_letter    TEXT,
        base_n           INTEGER,
        pct              DOUBLE,
        sig_higher_than  TEXT,
        source_file      TEXT
    )
    """)

    # ── category_norm ───────────────────────────────────────────────────
    con.execute("""
    CREATE TABLE IF NOT EXISTS ffx.category_norm (
        norm_label       TEXT,
        category_code    TEXT,
        category_name    TEXT,
        total            INTEGER,
        premium          INTEGER,
        standard_value   INTEGER,
        total_last_5y    INTEGER,
        pct_last_5y      DOUBLE,
        y2024            INTEGER,
        y2023            INTEGER,
        y2022            INTEGER,
        y2021            INTEGER,
        y2020            INTEGER,
        y2019            INTEGER,
        y2018            INTEGER,
        y2017            INTEGER,
        y2016            INTEGER
    )
    """)

    # ── norm_column_stat: rows 2-6 of Norm Data ─────────────────────────
    con.execute("""
    CREATE TABLE IF NOT EXISTS ffx.norm_column_stat (
        column_name TEXT NOT NULL,
        statistic   TEXT NOT NULL,
        value       DOUBLE,
        PRIMARY KEY (column_name, statistic)
    )
    """)

    # ── curated views (text-to-SQL surface) ─────────────────────────────
    con.execute("""
    CREATE OR REPLACE VIEW curated.product_test_v AS
    SELECT * FROM ffx.product_test
    """)

    con.execute("""
    CREATE OR REPLACE VIEW curated.measure_value_v AS
    SELECT pt.*, m.measure_code, m.measure_name, m.asked_of, m.is_calculated,
           mv.variant, mv.value
    FROM ffx.measure_value mv
    JOIN ffx.measure m         ON m.measure_id = mv.measure_id
    JOIN ffx.product_test pt   ON pt.product_test_id = mv.product_test_id
    """)

    con.execute("""
    CREATE OR REPLACE VIEW curated.session_report_v AS
    SELECT pt.*, sr.score_out_of_50, sr.category_average, sr.vs_category_norm,
           sr.star_5_pct, sr.star_4_pct, sr.star_3_pct,
           sr.star_2_pct, sr.star_1_pct,
           sr.award_quality, sr.award_taste, sr.award_value
    FROM ffx.session_report sr
    JOIN ffx.product_test pt ON pt.product_test_id = sr.product_test_id
    """)

    con.execute("""
    CREATE OR REPLACE VIEW curated.crosstab_v AS
    SELECT pt.cmr_ref, pt.product_name, pt.category_name, pt.session_set,
           cc.question_text, cc.response_option,
           cc.banner_group, cc.banner_level, cc.base_n, cc.pct,
           cc.sig_higher_than
    FROM ffx.crosstab_cell cc
    JOIN ffx.product_test pt ON pt.product_test_id = cc.product_test_id
    """)

    con.execute("""
    CREATE OR REPLACE VIEW curated.verbatim_v AS
    SELECT pt.cmr_ref, pt.product_name, pt.category_name, pt.session_set,
           pt.test_year, v.stars, v.text
    FROM ffx.verbatim v
    JOIN ffx.product_test pt ON pt.product_test_id = v.product_test_id
    WHERE v.pii_scrubbed
    """)


def load_df(con: duckdb.DuckDBPyConnection, table: str, df: pd.DataFrame,
            append: bool = True) -> int:
    """Load a DataFrame into a DuckDB table. Returns rows inserted."""
    if df.empty:
        return 0
    if append:
        con.execute(f"INSERT INTO {table} SELECT * FROM df")
    else:
        con.execute(f"DELETE FROM {table}")
        con.execute(f"INSERT INTO {table} SELECT * FROM df")
    return len(df)


def reset(con: duckdb.DuckDBPyConnection) -> None:
    """Drop and recreate all tables."""
    for schema in ("curated", "ffx", "meta"):
        for row in con.execute(
            f"SELECT table_name FROM information_schema.tables "
            f"WHERE table_schema = '{schema}' AND table_type = 'VIEW'"
        ).fetchall():
            con.execute(f"DROP VIEW IF EXISTS {schema}.{row[0]}")
        for row in con.execute(
            f"SELECT table_name FROM information_schema.tables "
            f"WHERE table_schema = '{schema}' AND table_type = 'BASE TABLE'"
        ).fetchall():
            con.execute(f"DROP TABLE IF EXISTS {schema}.{row[0]} CASCADE")
    create_schema(con)
