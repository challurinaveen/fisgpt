# ── F!S Internal GPT — production image ─────────────────────────
# Build:   docker build -t fis-gpt .
# Run:     docker run -p 8501:8501 --env-file .env fis-gpt
# ─────────────────────────────────────────────────────────────────

FROM python:3.11-slim AS base

# System deps for DuckDB, numpy, and general Python build
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        build-essential \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ── Install Python deps first (layer caching) ──────────────────
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Copy application code ──────────────────────────────────────
COPY config.py .
COPY phase1/ phase1/
COPY phase2/ phase2/
COPY phase3/ phase3/
COPY phase4/ phase4/
COPY phase5/ phase5/
COPY eval/ eval/
COPY db/ db/
COPY run_phase1.py run_phase2.py run_phase3.py run_phase4.py run_refresh.py ./

# ── Copy Streamlit config ──────────────────────────────────────
COPY .streamlit/ .streamlit/

# ── Pre-built database and embeddings ──────────────────────────
# Mount or copy these at runtime if the image doesn't ship them.
# COPY out/ out/

EXPOSE 8501

# Health check — Streamlit's /_stcore/health endpoint
HEALTHCHECK CMD curl --fail http://localhost:8501/_stcore/health || exit 1

ENTRYPOINT ["streamlit", "run", "phase5/app.py", \
            "--server.port=8501", \
            "--server.address=0.0.0.0"]
