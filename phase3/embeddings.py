"""
Phase 3 · Embedding generation and storage.

Generates vector embeddings for each text chunk and persists them as a
numpy array alongside the chunk index in JSON.

Embedding backends (in priority order):
  1. sentence-transformers  (all-MiniLM-L6-v2, 384 dims, ~80 MB download)
     Best quality-to-size ratio.  Runs on CPU in ~30 s for our chunk count.
  2. TF-IDF fallback (scikit-learn)
     If sentence-transformers is not installed, falls back to a simple
     TF-IDF vectoriser that produces sparse-then-SVD-reduced vectors.
     Keyword-heavy queries still work; semantic similarity is weaker.

The production deployment (Phase 5+) switches to text-embedding-3-large
(3 072 dims) behind an API call.  The cosine-similarity search layer in
search.py doesn't care which backend produced the vectors.
"""
from __future__ import annotations

import json
import pickle
import time
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

import config

if TYPE_CHECKING:
    from phase3.chunker import Chunk


EMBED_DIM_SBERT = 384          # all-MiniLM-L6-v2
EMBED_DIM_TFIDF = 256          # SVD-truncated TF-IDF
EMBED_NPY_PATH = config.OUT_DIR / "chunk_embeddings.npy"
CHUNK_INDEX_PATH = config.OUT_DIR / "chunk_index.json"
TFIDF_MODEL_PATH = config.OUT_DIR / "tfidf_model.pkl"


# ── backend detection ──────────────────────────────────────────────────

def _try_sbert():
    try:
        from sentence_transformers import SentenceTransformer  # noqa: F401
        return True
    except ImportError:
        return False


def _try_sklearn():
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer  # noqa: F401
        from sklearn.decomposition import TruncatedSVD  # noqa: F401
        return True
    except ImportError:
        return False


def detect_backend() -> str:
    """Return the best available backend name."""
    if _try_sbert():
        return "sbert"
    if _try_sklearn():
        return "tfidf"
    raise RuntimeError(
        "No embedding backend available.  Install one of:\n"
        "  pip install sentence-transformers   (recommended)\n"
        "  pip install scikit-learn             (lightweight fallback)"
    )


# ── embedding generators ──────────────────────────────────────────────

def _embed_sbert(texts: list[str]) -> np.ndarray:
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer("all-MiniLM-L6-v2")
    embeddings = model.encode(
        texts,
        show_progress_bar=True,
        batch_size=64,
        normalize_embeddings=True,
    )
    return np.array(embeddings, dtype=np.float32)


def _embed_tfidf(texts: list[str]) -> np.ndarray:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.decomposition import TruncatedSVD
    from sklearn.preprocessing import normalize

    dim = min(EMBED_DIM_TFIDF, len(texts) - 1)
    vectoriser = TfidfVectorizer(
        max_features=8000,
        sublinear_tf=True,
        stop_words="english",
    )
    tfidf_matrix = vectoriser.fit_transform(texts)
    svd = TruncatedSVD(n_components=dim, random_state=42)
    reduced = svd.fit_transform(tfidf_matrix)
    normalised = normalize(reduced, norm="l2")

    # Save fitted models so we can embed queries later
    with open(TFIDF_MODEL_PATH, "wb") as f:
        pickle.dump({"vectoriser": vectoriser, "svd": svd}, f)
    print(f"  saved TF-IDF model to {TFIDF_MODEL_PATH.name}")

    return normalised.astype(np.float32)


# ── public API ─────────────────────────────────────────────────────────

def embed_chunks(chunks: list["Chunk"]) -> tuple[np.ndarray, str]:
    """
    Generate embeddings for a list of Chunk objects.

    Returns (embeddings_array, backend_name).
    embeddings_array shape = (n_chunks, embed_dim).
    """
    backend = detect_backend()
    texts = [c.text for c in chunks]

    print(f"  embedding {len(texts)} chunks with backend '{backend}'...")
    t0 = time.time()

    if backend == "sbert":
        embeddings = _embed_sbert(texts)
    else:
        embeddings = _embed_tfidf(texts)

    elapsed = time.time() - t0
    print(f"  done in {elapsed:.1f}s — shape {embeddings.shape}")
    return embeddings, backend


def save_embeddings(
    chunks: list["Chunk"],
    embeddings: np.ndarray,
    backend: str,
) -> None:
    """Persist embeddings and chunk index to disk."""
    np.save(str(EMBED_NPY_PATH), embeddings)

    index = {
        "backend": backend,
        "embed_dim": int(embeddings.shape[1]),
        "n_chunks": len(chunks),
        "chunks": [],
    }
    for i, c in enumerate(chunks):
        index["chunks"].append({
            "idx": i,
            "chunk_id": c.chunk_id,
            "source_type": c.source_type,
            "source_file": c.source_file,
            "cmr_ref": c.cmr_ref,
            "category_name": c.category_name,
            "test_year": c.test_year,
            "locator": c.locator,
        })

    CHUNK_INDEX_PATH.write_text(
        json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"  saved {EMBED_NPY_PATH.name} ({embeddings.nbytes / 1024:.0f} KB)")
    print(f"  saved {CHUNK_INDEX_PATH.name}")


def load_embeddings() -> tuple[np.ndarray, dict]:
    """Load persisted embeddings and chunk index."""
    embeddings = np.load(str(EMBED_NPY_PATH))
    with open(CHUNK_INDEX_PATH, "r", encoding="utf-8") as f:
        index = json.load(f)
    return embeddings, index


def embed_query(query: str, backend: str | None = None) -> np.ndarray:
    """
    Embed a single query string using the same backend that built the index.

    Returns a 1-D vector.
    """
    if backend is None:
        backend = detect_backend()

    if backend == "sbert":
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer("all-MiniLM-L6-v2")
        vec = model.encode(
            [query], normalize_embeddings=True
        )[0]
        return np.array(vec, dtype=np.float32)
    else:
        # TF-IDF fallback — load the fitted vectoriser/SVD from disk
        from sklearn.preprocessing import normalize

        if not TFIDF_MODEL_PATH.exists():
            raise RuntimeError(
                "TF-IDF model not found. Run run_phase3.py first to build it."
            )
        with open(TFIDF_MODEL_PATH, "rb") as f:
            model = pickle.load(f)

        tfidf_vec = model["vectoriser"].transform([query])
        reduced = model["svd"].transform(tfidf_vec)
        normalised = normalize(reduced, norm="l2")
        return normalised[0].astype(np.float32)
