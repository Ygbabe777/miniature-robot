"""Embedding backend abstraction for the knowledge graph's vector search.

`EMBEDDING_BACKEND=none` (the default) uses a deterministic bag-of-words
hashed vector — good enough to support the demo's "find similar past
research" checks, but NOT a real semantic embedding. Real backends
(`openai`, `anthropic`, `local` sentence-transformers) are `TODO: REQUIRES
API` / `TODO: REQUIRES local model` — wire one up before trusting
similarity search for anything beyond the MVP demo.
"""
from __future__ import annotations

import hashlib
import re
from abc import ABC, abstractmethod

import numpy as np

from config import EmbeddingBackend, get_settings

_DIM = 256


class Embedder(ABC):
    backend_name: str

    @abstractmethod
    def embed(self, text: str) -> list[float]:
        raise NotImplementedError


class HashedBagOfWordsEmbedder(Embedder):
    """Deterministic, dependency-free fallback. NOT a semantic embedding —
    it captures lexical overlap only (two abstracts using different words
    for the same concept will not be found similar)."""

    backend_name = "hashed_bow"

    def embed(self, text: str) -> list[float]:
        vec = np.zeros(_DIM, dtype=float)
        for token in re.findall(r"[a-z0-9]+", text.lower()):
            idx = int(hashlib.sha256(token.encode()).hexdigest(), 16) % _DIM
            vec[idx] += 1.0
        norm = np.linalg.norm(vec)
        return (vec / norm if norm > 0 else vec).tolist()


class UnavailableEmbedder(Embedder):
    def __init__(self, backend_name: str, reason: str):
        self.backend_name = backend_name
        self.reason = reason

    def embed(self, text: str) -> list[float]:
        raise NotImplementedError(f"TODO: REQUIRES API — {self.backend_name} embedder not implemented: {self.reason}")


def get_embedder() -> Embedder:
    settings = get_settings()
    if settings.embedding_backend == EmbeddingBackend.NONE:
        return HashedBagOfWordsEmbedder()
    if settings.embedding_backend == EmbeddingBackend.OPENAI:
        return UnavailableEmbedder("openai", "requires OPENAI_API_KEY + openai SDK integration")
    if settings.embedding_backend == EmbeddingBackend.ANTHROPIC:
        return UnavailableEmbedder("anthropic", "Anthropic does not currently offer a public embeddings endpoint")
    if settings.embedding_backend == EmbeddingBackend.LOCAL:
        return UnavailableEmbedder("local", "requires a local sentence-transformers model download")
    return HashedBagOfWordsEmbedder()


def cosine_similarity(a: list[float], b: list[float]) -> float:
    va, vb = np.array(a), np.array(b)
    denom = np.linalg.norm(va) * np.linalg.norm(vb)
    return float(np.dot(va, vb) / denom) if denom > 0 else 0.0
