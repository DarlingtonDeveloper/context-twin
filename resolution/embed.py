"""Unit 3 — local, deterministic embeddings for the banded resolver.

Thin fastembed wrapper (BAAI/bge-small-en-v1.5), lazily loaded once. Kept self-contained
so Unit 3 builds in parallel without importing another unit's concrete code. NOTE for
integration: this is byte-identical in spirit to onboarding/embed.py — at merge, promote a
single `embed.py` to shared foundation and have both units import it.
"""
from __future__ import annotations
from typing import Protocol

import numpy as np

MODEL_NAME = "BAAI/bge-small-en-v1.5"


class Embedder(Protocol):
    def embed(self, texts: list[str]) -> np.ndarray: ...


class FastEmbedEmbedder:
    _model = None

    def embed(self, texts: list[str]) -> np.ndarray:
        if FastEmbedEmbedder._model is None:
            from fastembed import TextEmbedding
            FastEmbedEmbedder._model = TextEmbedding(model_name=MODEL_NAME)
        vecs = np.array(list(FastEmbedEmbedder._model.embed(list(texts))), dtype=np.float32)
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return vecs / norms


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    return 0.0 if denom == 0.0 else float(np.dot(a, b) / denom)


_default: Embedder | None = None


def default_embedder() -> Embedder:
    global _default
    if _default is None:
        _default = FastEmbedEmbedder()
    return _default
