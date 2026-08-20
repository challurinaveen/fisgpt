"""
Phase 3 · Retrieval — vector similarity search with keyword fallback.

Given a natural-language question, returns the top-k most relevant chunks
with source citations so the answering layer can ground its response.

Two retrieval modes:
  1. Vector search (cosine similarity) — when embeddings are available
  2. Keyword search (BM25-style term matching) — lightweight fallback

Both modes return the same SearchResult dataclass so the downstream
answering layer doesn't care which was used.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np

import config


@dataclass
class SearchResult:
    """A single search hit."""
    chunk_id:      int
    score:         float        # cosine similarity (vector) or BM25-ish (keyword)
    source_type:   str
    source_file:   str
    cmr_ref:       str | None
    category_name: str | None
    locator:       str          # human-readable citation
    text:          str


# ── vector search ──────────────────────────────────────────────────────

class VectorSearcher:
    """Brute-force cosine similarity over pre-computed embeddings."""

    def __init__(self):
        from phase3.embeddings import EMBED_NPY_PATH, CHUNK_INDEX_PATH

        self.embeddings = np.load(str(EMBED_NPY_PATH))
        with open(CHUNK_INDEX_PATH, "r", encoding="utf-8") as f:
            self.index = json.load(f)

        # Load full chunk texts for returning in results
        chunks_path = config.OUT_DIR / "chunks.json"
        with open(chunks_path, "r", encoding="utf-8") as f:
            self._chunk_texts = {c["chunk_id"]: c["text"] for c in json.load(f)}

        self.backend = self.index.get("backend", "sbert")

    def search(
        self,
        query: str,
        top_k: int = 5,
        source_types: list[str] | None = None,
        category: str | None = None,
    ) -> list[SearchResult]:
        """
        Find top-k chunks most similar to the query.

        Optional filters narrow the search before ranking.
        """
        from phase3.embeddings import embed_query
        q_vec = embed_query(query, self.backend)

        # Cosine similarity (embeddings are already L2-normalised)
        scores = self.embeddings @ q_vec

        # Build (index, score) pairs with optional filtering
        candidates = []
        for i, meta in enumerate(self.index["chunks"]):
            if source_types and meta["source_type"] not in source_types:
                continue
            if category and meta.get("category_name", ""):
                if category.lower() not in (meta["category_name"] or "").lower():
                    continue
            candidates.append((i, float(scores[i])))

        # Sort by score descending
        candidates.sort(key=lambda x: x[1], reverse=True)

        results = []
        for idx, score in candidates[:top_k]:
            meta = self.index["chunks"][idx]
            chunk_id = meta["chunk_id"]
            results.append(SearchResult(
                chunk_id=chunk_id,
                score=score,
                source_type=meta["source_type"],
                source_file=meta["source_file"],
                cmr_ref=meta.get("cmr_ref"),
                category_name=meta.get("category_name"),
                locator=meta["locator"],
                text=self._chunk_texts.get(chunk_id, ""),
            ))

        return results


# ── keyword search ─────────────────────────────────────────────────────

def keyword_search(
    query: str,
    top_k: int = 5,
    source_types: list[str] | None = None,
) -> list[SearchResult]:
    """
    Simple keyword-matching search over chunk texts.

    Uses term-frequency scoring: counts how many query terms appear
    in each chunk, weighted by term rarity across the corpus.
    No embeddings needed — works with just chunks.json.
    """
    chunks_path = config.OUT_DIR / "chunks.json"
    with open(chunks_path, "r", encoding="utf-8") as f:
        chunks = json.load(f)

    # Tokenise query
    query_terms = set(re.findall(r"\w+", query.lower()))
    if not query_terms:
        return []

    # Score each chunk
    scored = []
    for c in chunks:
        if source_types and c["source_type"] not in source_types:
            continue

        text_lower = c["text"].lower()
        text_tokens = set(re.findall(r"\w+", text_lower))

        # Count matching terms
        matches = query_terms & text_tokens
        if not matches:
            continue

        # Simple BM25-ish score: sum of 1/log(term_freq+1) for matching terms
        score = 0.0
        for term in matches:
            # Bonus for exact phrase match
            if term in text_lower:
                score += 1.0
            # Bonus for appearing in first line (title/header)
            first_line = text_lower.split("\n")[0]
            if term in first_line:
                score += 0.5

        scored.append((c, score))

    # Sort by score descending
    scored.sort(key=lambda x: x[1], reverse=True)

    results = []
    for c, score in scored[:top_k]:
        results.append(SearchResult(
            chunk_id=c["chunk_id"],
            score=score,
            source_type=c["source_type"],
            source_file=c["source_file"],
            cmr_ref=c.get("cmr_ref"),
            category_name=c.get("category_name"),
            locator=c["locator"],
            text=c["text"],
        ))

    return results


# ── unified search ─────────────────────────────────────────────────────

def search(
    query: str,
    top_k: int = 5,
    source_types: list[str] | None = None,
    category: str | None = None,
) -> list[SearchResult]:
    """
    Search for relevant chunks — uses vector search if available,
    otherwise falls back to keyword search.
    """
    try:
        searcher = VectorSearcher()
        return searcher.search(query, top_k, source_types, category)
    except Exception:
        # Fall back to keyword search
        return keyword_search(query, top_k, source_types)


def format_citations(results: list[SearchResult]) -> str:
    """Format search results as a citation block for the answering layer."""
    if not results:
        return "No relevant documents found."

    parts = []
    for i, r in enumerate(results, 1):
        header = f"[{i}] {r.locator} (score: {r.score:.3f})"
        parts.append(f"{header}\n{r.text}")

    return "\n\n---\n\n".join(parts)
