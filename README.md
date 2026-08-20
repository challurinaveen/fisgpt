# 🍽️ F!S Internal GPT

Internal AI tool for **F!S Group** — asks questions about 30+ years of FoodFax product testing data and gets answers with citations.

25,000+ product tests · 460+ categories · 43 sensory measures · consumer verbatims · category norms

---

## What it does

| Page | What you get |
|------|-------------|
| **💬 Chat** | Ask anything in plain English. The AI queries the database and searches documents, then answers with source citations. |
| **🔍 Data Explorer** | Browse products, categories, 2025 session results, and measures with filters — no SQL needed. |
| **📊 Dashboard** | Charts and metrics across the full test history: tests per year, top categories, brand vs own-label, scores. |

---

## Deploy to Streamlit Cloud (recommended)

### 1. Push to GitHub

Create a **private** repo on [github.com/new](https://github.com/new) (no README, no .gitignore). Then:

```bash
cd fis-gpt
git remote add origin https://github.com/YOUR_USERNAME/fis-gpt.git
git push -u origin main
```

### 2. Deploy

1. Go to [share.streamlit.io](https://share.streamlit.io) → sign in with GitHub
2. Click **New app**
3. Select your `fis-gpt` repo, branch `main`, main file: **`phase5/app.py`**
4. Click **Advanced settings** before deploying

### 3. Add secrets

In **Advanced settings → Secrets**, paste:

```toml
OPENAI_API_KEY = "sk-your-key-here"

# Optional: gate access with a password
# APP_PASSWORD = "your-team-password"
```

Click **Deploy**. Your team can access it at `https://your-app.streamlit.app`.

---

## Run locally

```bash
cd fis-gpt
pip install -r requirements.txt
```

Create `.env` in the `fis-gpt/` folder:

```env
OPENAI_API_KEY=sk-...
```

Launch:

```bash
streamlit run phase5/app.py
```

Open [http://localhost:8501](http://localhost:8501).

---

## Data refresh

When new FoodFax data arrives (new session PDFs, updated Excel files):

1. Drop the new files into `Fis Group/fis group data/Project Data/FoodFax/`
2. Rebuild:

```bash
python run_refresh.py
```

3. Commit the updated `out/` files and push to redeploy.

Flags:
- `--skip-phase1` — skip corpus profiling (no new files added)
- `--skip-embeddings` — skip re-embedding (only new products, not new doc types)

---

## Docker (alternative)

```bash
cd fis-gpt
docker compose up -d
```

Or manually:

```bash
docker build -t fis-gpt .
docker run -d -p 8501:8501 --env-file .env -v ./out:/app/out:ro fis-gpt
```

---

## Project structure

```
fis-gpt/
├── phase1/            Corpus profiling & audit
├── phase2/            DuckDB warehouse builder
├── phase3/            RAG pipeline (chunking + TF-IDF search)
├── phase4/            LLM answering layer (OpenAI / Claude / Gemini)
├── phase5/            Streamlit web UI
│   ├── app.py           Entry point
│   ├── auth.py          Password authentication
│   ├── shared.py        Sidebar, branding, DB helpers
│   └── pages/
│       ├── chat.py      Chat interface
│       ├── explorer.py  Data Explorer (4 tabs)
│       └── dashboard.py Dashboard with charts
├── eval/              60-question evaluation suite
├── out/               Pre-built database + embeddings (tracked in git)
├── config.py          Corpus paths and known-shape registry
├── run_refresh.py     Full data rebuild script
├── requirements.txt
├── Dockerfile
└── docker-compose.yml
```

---

## LLM providers

Configure at least one in `.env` (or Streamlit Cloud secrets):

| Provider | Models | Env variable |
|----------|--------|-------------|
| OpenAI | gpt-4o-mini, gpt-4o | `OPENAI_API_KEY` |
| Anthropic | Claude Sonnet, Opus | `ANTHROPIC_API_KEY` |
| Google | Gemini Pro, Flash | `GOOGLE_API_KEY` |

The sidebar auto-detects which are available.

---

## Security

- API keys stay in `.env` / Streamlit secrets — never logged or exposed
- Database is **read-only** in the web UI
- Optional `APP_PASSWORD` for team-only access
- XSRF protection enabled, CORS disabled

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Chat gives empty answers | Check your API key is set (`.env` or Streamlit secrets) |
| `FileNotFoundError: fis_warehouse.duckdb` | Run `python run_refresh.py` to build the database |
| Missing module errors | `pip install -r requirements.txt` |
| Stale data after refresh | Restart the Streamlit server |
| No login form | Set `APP_PASSWORD` to enable authentication |

---

*F!S Group · FoodFax product testing · Internal use only*
