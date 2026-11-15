"""Text embeddings for tagging, clustering and ranking (docs/PLAN.md §13, §15).

`FastEmbedder` runs `paraphrase-multilingual-MiniLM-L12-v2` locally through fastembed
(ONNX on CPU, no key, no network after the first model download). `HashEmbedder` is a
deterministic bag-of-words stand-in used by tests and DIGEST_OFFLINE=1, so neither ever
downloads the model.

Every embedder returns float32 rows of unit length, so a dot product is the cosine.
"""

import hashlib
import logging
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from digest.config import EmbedConfig

log = logging.getLogger(__name__)

DIM = 384  # items.embedding is halfvec(384)


class Embedder(Protocol):
    @property
    def name(self) -> str: ...

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        """One unit-length float32 row of DIM values per text."""
        ...


def normalize_rows(vectors: np.ndarray) -> np.ndarray:
    vectors = np.asarray(vectors, dtype=np.float32)
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    return vectors / np.where(norms == 0, 1, norms)


def item_text(title: str, description: str | None, max_chars: int) -> str:
    """What an item is embedded from: title + description. Never the full article."""
    text = title.strip()
    if description and description.strip():
        text += "\n" + description.strip()
    return text[:max_chars]


class FastEmbedder:
    def __init__(self, cfg: EmbedConfig) -> None:
        self._cfg = cfg
        self._model: Any = None

    @property
    def name(self) -> str:
        return self._cfg.model

    def _load(self) -> Any:
        if self._model is None:
            from fastembed import TextEmbedding  # heavy import; only when really embedding

            cache = Path(self._cfg.cache_dir).expanduser()
            log.info("embed: loading %s (cache %s)", self._cfg.model, cache)
            self._model = TextEmbedding(
                model_name=self._cfg.model, cache_dir=str(cache), threads=self._cfg.threads
            )
        return self._model

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, DIM), dtype=np.float32)
        rows = list(self._load().embed(list(texts), batch_size=self._cfg.batch_size))
        vectors = normalize_rows(np.vstack(rows))
        if vectors.shape[1] != DIM:
            raise ValueError(f"{self._cfg.model} returned {vectors.shape[1]} dims, want {DIM}")
        return vectors


_TOKEN = re.compile(r"\w+")


class HashEmbedder:
    """Feature-hashed bag of words: texts sharing words are similar, identical texts have
    cosine 1. Good enough to exercise tagging and clustering without a model."""

    name = "hash-bow"

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        out = np.zeros((len(texts), DIM), dtype=np.float32)
        for row, text in enumerate(texts):
            for token in _TOKEN.findall(text.lower()):
                h = int.from_bytes(hashlib.sha256(token.encode()).digest()[:4], "big")
                out[row, h % DIM] += 1.0 if h & 0x80000000 else -1.0
            if not out[row].any():
                out[row, 0] = 1.0
        return normalize_rows(out)


def make_embedder(cfg: EmbedConfig, *, offline: bool) -> Embedder:
    return HashEmbedder() if offline else FastEmbedder(cfg)
