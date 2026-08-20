# F!S Internal GPT

An internal AI-powered tool for **F!S Group** that analyses 30+ years of FoodFax product testing data — proposals, reports, Excel databases — and answers business queries with citations.

Built for the F!S consultancy team to quickly query product scores, category norms, consumer verbatims, and methodology details across 25,000+ product tests.

---

## Features

| Feature | Description |
|---------|-------------|
| **💬 Chat** | Ask natural-language questions about FoodFax data. The LLM queries the database via SQL and searches document chunks, returning answers with inline citations. |
| **🔍 Data Explorer** | Browse products, categories, 2025 session results, and measures through a filterable UI — no SQL required. |
| **📊 Dashboard** | At-a-glance metrics, charts, and trends across the full 25,000+ test history. |
| **🔒 Authentication** | Optional password gate for internal-only access. |

---

## Quick Start

### 1. Install dependencies

```bash
cd fis-gpt
python -m pip install -r requirements.txt
```

Requires **Python 3.10+**. The main dependencies are:

- `duckdb` — local analytical database
- `streamlit` — web UI framework
- `openai` / `anthropic` / `google-genai` — LLM providers (install at least one)
- `scikit-learn` — TF-IDF embeddings (lightweight fallback)
- `pandas`, `numpy`, `openpyxl`, `pypdf` — data parsing

### 2. Configure API keys

Create a `.env` file in the `fis-gpt/` directory:

```env
# At least one LLM provider key is required
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
GOOGLE_API_KEY=AIza...

# Optional: set a password to gate access
APP_PASSWORD=your-team-password
```

### 3. Build the data warehouse

The corpus folder (`Fis Group/`) must be in the parent directory of `fis-gpt/`.

```bash
# Full build: profile → warehouse → chunks & embeddings
python fis-gpt/run_refresh.py
```

This creates:
- `fis-gpt/out/fis_warehouse.duckdb` — the local DuckDB database (25,914 product tests)
- `fis-gpt/out/chunks.json` — 357 document chunks for RAG retrieval
- `fis-gpt/out/chunk_embeddings.npy` — TF-IDF vectors (256 dims)

### 4. Launch the app

```bash
streamlit run fis-gpt/phase5/app.py
```

Open [http://localhost:8501](http://localhost:8501) in your browser.

---

## Data Refresh

When new FoodFax data arrives (new session PDFs, updated Excel files):

1. Drop the new files into the corpus folder (`Fis Group/fis group data/Project Data/FoodFax/`)
2. Run the refresh script:

```bash
# Full rebuild
python fis-gpt/run_refresh.py

# Skip Phase 1 if no new files were added (just data changes)
python fis-gpt/run_refresh.py --skip-phase1

# Skip embeddings if only new products, not new document types
python fis-gpt/run_refresh.py --skip-embeddings
```

3. Restart the Streamlit server to pick up changes.

---

## Docker Deployment

### Build & run

```bash
cd fis-gpt

# Build the image
docker build -t fis-gpt .

# Run with your .env file and pre-built data
docker run -d \
  --name fis-gpt \
  -p 8501:8501 \
  --env-file .env \
  -v ./out:/app/out:ro \
  fis-gpt
```

### Docker Compose (recommended)

```bash
cd fis-gpt
docker compose up -d
```

The app will be available at [http://localhost:8501](http://localhost:8501).

To stop: `docker compose down`

---

## Project Architecture

```
fis-gpt/
├── config.py              # Paths, known-shape registry, walk rules
├── requirements.txt       # Python dependencies
├── .env                   # API keys (not committed)
├── .gitignore
├── Dockerfile
├── docker-compose.yml
│
├── phase1/                # Corpus profiling & audit
│   ├── inventory.py       # Walk the corpus tree
│   ├── profile_excel.py   # Detect Excel sheet shapes
│   ├── profile_pdf.py     # Extract PDF structure
│   ├── dictionary.py      # Build measure dictionary
│   ├── crosscheck.py      # Cross-source validation
│   └── report.py          # Generate audit report
│
├── phase2/                # DuckDB warehouse build
│   ├── warehouse.py       # Schema creation, connection mgmt
│   ├── parse_norm.py      # Parse Norm Data sheet
│   ├── parse_historic.py  # Parse Historic Products sheet
│   ├── parse_pre2021.py   # Parse pre-2021 database
│   ├── parse_crosstabs.py # Parse banner crosstab tables
│   ├── load_pdfs.py       # Load session PDF records
│   ├── load_measures.py   # Build measure dictionary table
│   └── verify.py          # Golden-question verification
│
├── phase3/                # RAG pipeline
│   ├── chunker.py         # Document chunking (6 source types)
│   ├── embeddings.py      # TF-IDF vectorisation + SVD
│   └── search.py          # Hybrid keyword + vector search
│
├── phase4/                # LLM answering layer
│   ├── providers.py       # Multi-provider abstraction (Claude, GPT, Gemini)
│   ├── prompts.py         # System prompt + tool definitions
│   ├── tools.py           # run_sql, search_docs tool execution
│   └── answerer.py        # Orchestration: prompt → tool loop → response
│
├── phase5/                # Streamlit web UI
│   ├── app.py             # Multi-page app entry point
│   ├── auth.py            # Password authentication
│   ├── shared.py          # Sidebar, branding, DB helpers
│   └── pages/
│       ├── chat.py        # Chat interface with sample questions
│       ├── explorer.py    # Data Explorer (4 tabs)
│       └── dashboard.py   # Dashboard with charts & metrics
│
├── eval/                  # Evaluation suite
│   └── golden_questions.yaml  # 60 test questions (4 routes)
│
├── db/migrations/
│   └── 001_init.sql       # DuckDB schema DDL
│
├── run_phase1.py          # Phase 1 runner
├── run_phase2.py          # Phase 2 runner
├── run_phase3.py          # Phase 3 runner
├── run_phase4.py          # Phase 4 evaluation runner
├── run_refresh.py         # Full data refresh (Phases 1→2→3)
│
├── .streamlit/
│   └── config.toml        # Streamlit theme & server config
│
└── out/                   # Build outputs (not committed)
    ├── fis_warehouse.duckdb
    ├── chunks.json
    ├── chunk_embeddings.npy
    └── ...
```

---

## LLM Providers

The app supports three LLM providers. Configure at least one in `.env`:

| Provider | Model | Key variable |
|----------|-------|--------------|
| OpenAI | gpt-4o-mini / gpt-4o | `OPENAI_API_KEY` |
| Anthropic | Claude 3.5 Sonnet / Opus | `ANTHROPIC_API_KEY` |
| Google | Gemini 1.5 Pro / Flash | `GOOGLE_API_KEY` |

The UI auto-detects which providers are available and shows a model selector in the sidebar.

---

## Evaluation

Run the 60-question evaluation suite to verify answer quality:

```bash
python fis-gpt/run_phase4.py
```

Ship gates (Phase 4 targets):
- **Numeric accuracy**: ≥ 90% of measure/score queries return correct values
- **Citation precision**: ≥ 85% of document-route answers cite the right source
- **Refusal rate**: 100% of out-of-scope questions are correctly refused
- **Zero fabrication**: No invented product names, scores, or facts

Current results: **90% overall** (54/60) with gpt-4o-mini.

---

## Security Notes

- API keys are loaded from `.env` and never logged or transmitted beyond the provider APIs
- The DuckDB database is opened **read-only** in the web UI — no writes possible
- SQL tool calls are wrapped in read-only transactions
- The optional `APP_PASSWORD` provides a simple team-access gate
- XSRF protection is enabled in the Streamlit config
- CORS is disabled (same-origin only)

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `ModuleNotFoundError: No module named 'openai'` | `pip install openai` (or `anthropic` / `google-genai`) |
| `ModuleNotFoundError: No module named 'scikit-learn'` | `pip install scikit-learn` |
| `FileNotFoundError: fis_warehouse.duckdb` | Run `python fis-gpt/run_refresh.py` to build the database |
| Stale data after refresh | Restart the Streamlit server (`Ctrl+C` then relaunch) |
| Chat returns empty answers | Check that at least one `*_API_KEY` is set in `.env` |
| Login form not showing | Set `APP_PASSWORD=...` in `.env` to enable auth |

---

*Built for F!S Group · FoodFax product testing data · 2025*
