-- ===========================================================================
-- F!S Internal GPT · warehouse schema, migration 001
--
-- Postgres 16 + pgvector. One database holds both the measure warehouse and
-- the document vector index so that a query can filter on category/year and
-- rank by semantic similarity in a single statement (build plan §03).
--
-- Column choices below come from the Phase 1 profiling run, not from guesswork:
-- see fis-gpt/out/excel_columns.csv for the source of every field.
-- ===========================================================================

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE SCHEMA IF NOT EXISTS ffx;      -- FoodFax measures
CREATE SCHEMA IF NOT EXISTS docs;     -- unstructured corpus
CREATE SCHEMA IF NOT EXISTS meta;     -- lineage, dictionary, audit
CREATE SCHEMA IF NOT EXISTS curated;  -- the only surface text-to-SQL may read


-- ---------------------------------------------------------------------------
-- meta: provenance. Every row in the warehouse traces to a file and a sheet.
-- ---------------------------------------------------------------------------
CREATE TABLE meta.source_file (
    source_file_id   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    sharepoint_id    TEXT,
    drive_id         TEXT,
    relative_path    TEXT        NOT NULL,
    file_name        TEXT        NOT NULL,
    content_sha256   CHAR(64)    NOT NULL,
    size_bytes       BIGINT      NOT NULL,
    last_modified    TIMESTAMPTZ,
    ingested_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Entra ID group object IDs permitted to see anything derived from this
    -- file. Mirrored from Graph at sync; enforced in WHERE clauses, never in
    -- a prompt (build plan §06).
    allowed_group_ids UUID[]     NOT NULL DEFAULT '{}',
    is_commercial     BOOLEAN    NOT NULL DEFAULT FALSE,
    UNIQUE (content_sha256)
);
COMMENT ON COLUMN meta.source_file.content_sha256 IS
  'Content hash. The Archive/ tree is a byte-identical duplicate of the live '
  'corpus (Phase 1 finding: 143 MB, 368 files); dedupe happens on this column '
  'before chunking so answers never cite the same content twice.';

CREATE TABLE meta.sheet_ingest (
    sheet_ingest_id  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_file_id   BIGINT      NOT NULL REFERENCES meta.source_file,
    sheet_name       TEXT        NOT NULL,
    detected_shape   TEXT        NOT NULL,
    header_row       INT         NOT NULL,
    header_confidence NUMERIC(4,3),
    rows_read        INT,
    ingested         BOOLEAN     NOT NULL,
    skip_reason      TEXT,
    assertion_error  TEXT,
    UNIQUE (source_file_id, sheet_name)
);
COMMENT ON TABLE meta.sheet_ingest IS
  'One row per sheet seen, ingested or not. 230 of 347 sheets in the Phase 1 '
  'corpus are report-rendering surfaces rather than data; recording the skip '
  'makes that visible instead of silent.';


-- ---------------------------------------------------------------------------
-- ffx: dimensions
-- ---------------------------------------------------------------------------
CREATE TABLE ffx.category (
    category_id      BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    category_code    TEXT,
    category_name    TEXT NOT NULL,
    short_name       TEXT,
    UNIQUE (category_name)
);

CREATE TABLE ffx.manufacturer (
    manufacturer_id   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    manufacturer_code TEXT,
    manufacturer_name TEXT NOT NULL,
    own_label_or_brand TEXT,          -- 'Own Label' | 'Brand'
    UNIQUE (manufacturer_name)
);

CREATE TABLE ffx.project (
    project_id       BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    project_code     TEXT NOT NULL,           -- 'FFX2023', 'Granville', 'Leather'
    project_name     TEXT,
    project_type     TEXT,                    -- FFX | GOLA | NPA | BEA | GAS | OMNI
    -- 41 project code names cover 13,278 historic tests. Until the business
    -- supplies the mapping (kickoff Q4) this stays NULL and client-level
    -- questions degrade to project-level rather than being answered wrongly.
    client_name      TEXT,
    client_mapping_source TEXT,
    UNIQUE (project_code)
);


-- ---------------------------------------------------------------------------
-- ffx: the product test — the grain of the whole warehouse
-- ---------------------------------------------------------------------------
CREATE TABLE ffx.product_test (
    product_test_id  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    unique_reference TEXT UNIQUE,             -- 'FFX2023_220461'
    cmr_ref          TEXT,                    -- six-digit join key
    prodref          TEXT,
    project_id       BIGINT REFERENCES ffx.project,
    category_id      BIGINT REFERENCES ffx.category,
    manufacturer_id  BIGINT REFERENCES ffx.manufacturer,
    product_name     TEXT,
    test_year        INT,
    test_date        DATE,
    session_set      TEXT,                    -- 'Set 26'
    test_order       INT,
    price_gbp        NUMERIC(10,2),
    pack_size        TEXT,
    storage          TEXT,                    -- Ambient | Chilled | Frozen
    sweet_or_savoury TEXT,
    fax_type         TEXT,                    -- Food | Soft Drink | Hot Drink ...
    tier             TEXT,                    -- Premium | Standard | Value
    sample_size      INT,
    -- 5-, 7- and 9-point scales all exist in the archive. Carrying the scale
    -- makes it possible to refuse a cross-scale comparison rather than
    -- silently make one (build plan §05).
    attribute_scale  SMALLINT CHECK (attribute_scale IN (5, 7, 9)),
    source_file_id   BIGINT NOT NULL REFERENCES meta.source_file
);
CREATE INDEX ON ffx.product_test (cmr_ref);
CREATE INDEX ON ffx.product_test (test_year);
CREATE INDEX ON ffx.product_test (category_id, test_year);
CREATE INDEX ON ffx.product_test USING gin (product_name gin_trgm_ops);


-- ---------------------------------------------------------------------------
-- ffx: measures, long-form.
--
-- Norm Data is 113 columns wide: ~30 attributes × {mean score, T2B%}. Kept
-- wide it is unqueryable; melted it is one clean fact table.
-- ---------------------------------------------------------------------------
CREATE TABLE ffx.measure (
    measure_id       BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    measure_code     TEXT UNIQUE NOT NULL,     -- '3a'
    measure_name     TEXT NOT NULL,            -- 'Taste'
    asked_of         TEXT,                     -- 'All' | 'Drinks' | 'Fresh Produce'
    is_calculated    BOOLEAN NOT NULL DEFAULT FALSE,
    definition       TEXT,
    report_templates TEXT,
    known_clients    TEXT
);
COMMENT ON TABLE ffx.measure IS
  'Seeded from Foodfax Database details.xlsx. 35 of 36 measures mapped to '
  'physical columns in Phase 1; see out/measure_mapping.csv.';

CREATE TABLE ffx.measure_value (
    product_test_id  BIGINT NOT NULL REFERENCES ffx.product_test,
    measure_id       BIGINT NOT NULL REFERENCES ffx.measure,
    variant          TEXT   NOT NULL,          -- MEAN | T2B | T3B | T4B | B2B
    value            NUMERIC(10,4),
    base_n           INT,
    source_file_id   BIGINT NOT NULL REFERENCES meta.source_file,
    PRIMARY KEY (product_test_id, measure_id, variant)
);
CREATE INDEX ON ffx.measure_value (measure_id, variant);

-- Rows 2-6 of Norm Data are column-level statistics over the whole corpus.
-- They are the benchmark the client reports compare a product against, so
-- they are data in their own right, not noise above the header.
CREATE TABLE ffx.norm_column_stat (
    measure_id       BIGINT NOT NULL REFERENCES ffx.measure,
    variant          TEXT   NOT NULL,
    norm_label       TEXT   NOT NULL,          -- 'NORM Jan 2025'
    statistic        TEXT   NOT NULL,          -- mean | min | max | sd | sem
    value            NUMERIC(14,8),
    n_products       INT,
    source_file_id   BIGINT NOT NULL REFERENCES meta.source_file,
    PRIMARY KEY (measure_id, variant, norm_label, statistic)
);

CREATE TABLE ffx.category_norm (
    category_id      BIGINT NOT NULL REFERENCES ffx.category,
    norm_label       TEXT   NOT NULL,
    test_year        INT,
    tier             TEXT,
    n_products       INT,
    PRIMARY KEY (category_id, norm_label, test_year, tier)
);


-- ---------------------------------------------------------------------------
-- ffx: banner crosstabs, long-form.
--
-- The `Data` sheet of each FFX Tables workbook is 11,625 rows of every
-- question cut by Gender / Age / Lifestage / SEG / Region / NPS, with base
-- sizes and A/B/C significance letters carried in the header.
-- ---------------------------------------------------------------------------
CREATE TABLE ffx.crosstab_cell (
    crosstab_cell_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    product_test_id  BIGINT REFERENCES ffx.product_test,
    cmr_ref          TEXT,
    question_text    TEXT   NOT NULL,
    response_option  TEXT,                     -- 'Top 2 box'
    banner_group     TEXT   NOT NULL,          -- 'Age Groupings'
    banner_level     TEXT   NOT NULL,          -- '18 - 34'
    banner_letter    CHAR(1),                  -- significance-test column code
    base_n           INT,
    pct              NUMERIC(7,4),
    sig_higher_than  TEXT,                     -- e.g. 'BC'
    source_file_id   BIGINT NOT NULL REFERENCES meta.source_file
);
CREATE INDEX ON ffx.crosstab_cell (cmr_ref);
CREATE INDEX ON ffx.crosstab_cell (banner_group, banner_level);


-- ---------------------------------------------------------------------------
-- ffx: session report facts parsed from the one-page PDFs
-- ---------------------------------------------------------------------------
CREATE TABLE ffx.session_report (
    product_test_id  BIGINT PRIMARY KEY REFERENCES ffx.product_test,
    score_out_of_50  INT,
    category_average NUMERIC(5,2),
    vs_category_norm NUMERIC(5,2) GENERATED ALWAYS AS
                     (score_out_of_50 - category_average) STORED,
    star_5_pct       INT, star_4_pct INT, star_3_pct INT,
    star_2_pct       INT, star_1_pct INT,
    award_quality    BOOLEAN,
    award_taste      BOOLEAN,
    award_value      BOOLEAN,
    nutrition        JSONB,
    pack_shot_path   TEXT,
    source_file_id   BIGINT NOT NULL REFERENCES meta.source_file,
    -- All 59 Phase 1 reports satisfy this. A future set that does not is a
    -- parser failure to investigate, not a row to accept.
    CONSTRAINT star_pcts_sum_to_100 CHECK (
        star_5_pct + star_4_pct + star_3_pct + star_2_pct + star_1_pct
        BETWEEN 98 AND 102)
);

CREATE TABLE ffx.category_award (
    product_test_id  BIGINT NOT NULL REFERENCES ffx.product_test,
    award_label      TEXT   NOT NULL,          -- 'Weekend treat'
    pct              INT    NOT NULL,
    rank             SMALLINT,
    PRIMARY KEY (product_test_id, award_label)
);

CREATE TABLE ffx.verbatim (
    verbatim_id      BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    product_test_id  BIGINT NOT NULL REFERENCES ffx.product_test,
    respondent_ref   TEXT,
    stars            SMALLINT CHECK (stars BETWEEN 1 AND 5),
    text             TEXT NOT NULL,
    -- Free text from real members of the 25,000-person panel. Scrubbed at
    -- ingest; the flag records that the scrub ran, so an unscrubbed backfill
    -- is detectable (build plan §06).
    pii_scrubbed     BOOLEAN NOT NULL DEFAULT FALSE,
    pii_entities_removed INT NOT NULL DEFAULT 0,
    source_file_id   BIGINT NOT NULL REFERENCES meta.source_file
);
CREATE INDEX ON ffx.verbatim USING gin (text gin_trgm_ops);


-- ---------------------------------------------------------------------------
-- docs: the vector side
-- ---------------------------------------------------------------------------
CREATE TABLE docs.chunk (
    chunk_id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_file_id   BIGINT NOT NULL REFERENCES meta.source_file,
    source_type      TEXT   NOT NULL,          -- session_report | proposal | tracker | verbatim
    locator          TEXT   NOT NULL,          -- 'Slide 4' | 'Sheet Tables, rows 12-31'
    ordinal          INT    NOT NULL,
    text             TEXT   NOT NULL,
    -- Denormalised for pre-filtering: a vector search that has to join before
    -- it can filter cannot use the index efficiently.
    client_name      TEXT,
    category_name    TEXT,
    test_year        INT,
    cmr_ref          TEXT,
    allowed_group_ids UUID[] NOT NULL DEFAULT '{}',
    is_commercial    BOOLEAN NOT NULL DEFAULT FALSE,
    embedding        vector(3072),             -- text-embedding-3-large
    UNIQUE (source_file_id, ordinal)
);
CREATE INDEX ON docs.chunk USING hnsw (embedding vector_cosine_ops);
CREATE INDEX ON docs.chunk (source_type, test_year);
CREATE INDEX ON docs.chunk USING gin (allowed_group_ids);


-- ---------------------------------------------------------------------------
-- meta: audit. Every answer must be reconstructable.
-- ---------------------------------------------------------------------------
CREATE TABLE meta.query_log (
    query_log_id     BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    asked_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    user_upn         TEXT NOT NULL,
    user_group_ids   UUID[] NOT NULL,
    question         TEXT NOT NULL,
    route            TEXT,                     -- document | measure | hybrid | refused
    tools_called     JSONB,
    sql_executed     TEXT[],
    chunk_ids        BIGINT[],
    answer           TEXT,
    model_id         TEXT,
    prompt_version   TEXT,
    latency_ms       INT,
    input_tokens     INT,
    output_tokens    INT
);
CREATE INDEX ON meta.query_log (asked_at DESC);


-- ---------------------------------------------------------------------------
-- curated: the ONLY schema the text-to-SQL tool is granted SELECT on.
-- ---------------------------------------------------------------------------
CREATE VIEW curated.product_test_v AS
SELECT pt.product_test_id,
       pt.unique_reference, pt.cmr_ref, pt.product_name,
       c.category_name, c.short_name AS category_short_name,
       m.manufacturer_name, m.own_label_or_brand,
       p.project_code, p.project_type, p.client_name,
       pt.test_year, pt.test_date, pt.session_set,
       pt.price_gbp, pt.pack_size, pt.storage, pt.sweet_or_savoury,
       pt.fax_type, pt.tier, pt.sample_size, pt.attribute_scale,
       sf.allowed_group_ids, sf.is_commercial
FROM ffx.product_test pt
LEFT JOIN ffx.category     c  ON c.category_id     = pt.category_id
LEFT JOIN ffx.manufacturer m  ON m.manufacturer_id = pt.manufacturer_id
LEFT JOIN ffx.project      p  ON p.project_id      = pt.project_id
JOIN      meta.source_file sf ON sf.source_file_id = pt.source_file_id;

CREATE VIEW curated.measure_value_v AS
SELECT ptv.*,
       ms.measure_code, ms.measure_name, ms.asked_of, ms.is_calculated,
       mv.variant, mv.value, mv.base_n
FROM ffx.measure_value mv
JOIN ffx.measure ms          ON ms.measure_id = mv.measure_id
JOIN curated.product_test_v ptv USING (product_test_id);

CREATE VIEW curated.session_report_v AS
SELECT ptv.*,
       sr.score_out_of_50, sr.category_average, sr.vs_category_norm,
       sr.star_5_pct, sr.star_4_pct, sr.star_3_pct, sr.star_2_pct, sr.star_1_pct,
       sr.award_quality, sr.award_taste, sr.award_value, sr.nutrition
FROM ffx.session_report sr
JOIN curated.product_test_v ptv USING (product_test_id);

CREATE VIEW curated.crosstab_v AS
SELECT ptv.cmr_ref, ptv.product_name, ptv.category_name, ptv.session_set,
       cc.question_text, cc.response_option,
       cc.banner_group, cc.banner_level, cc.base_n, cc.pct, cc.sig_higher_than,
       ptv.allowed_group_ids, ptv.is_commercial
FROM ffx.crosstab_cell cc
JOIN curated.product_test_v ptv USING (product_test_id);

CREATE VIEW curated.verbatim_v AS
SELECT ptv.cmr_ref, ptv.product_name, ptv.category_name, ptv.session_set,
       ptv.test_year, v.stars, v.text,
       ptv.allowed_group_ids, ptv.is_commercial
FROM ffx.verbatim v
JOIN curated.product_test_v ptv USING (product_test_id)
WHERE v.pii_scrubbed;          -- unscrubbed verbatims are never queryable


-- ---------------------------------------------------------------------------
-- Roles. The answering agent connects as ffx_reader and can reach nothing
-- except curated.*, so a text-to-SQL mistake cannot read raw or write anything.
-- ---------------------------------------------------------------------------
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ffx_reader') THEN
        CREATE ROLE ffx_reader NOLOGIN;
    END IF;
END $$;

GRANT USAGE ON SCHEMA curated TO ffx_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA curated TO ffx_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA curated GRANT SELECT ON TABLES TO ffx_reader;
REVOKE ALL ON SCHEMA ffx, docs, meta FROM ffx_reader;

ALTER ROLE ffx_reader SET statement_timeout = '15s';
ALTER ROLE ffx_reader SET default_transaction_read_only = on;
