"""Embeddings.

Default (when OPENAI_API_KEY is set): OpenAI `text-embedding-3-small` through LangChain, cached in Redis.
Fallback (no key / package missing): a deterministic *hashing* embedder (word + char n-grams). It is lexical,
not truly semantic, but keeps the whole app usable and testable offline.

"""
from __future__ import annotations

import re
import zlib

import numpy as np

from ..config import get_settings
from . import cache

HASH_DIM = 512
_lc_embeddings = None
_lc_error: str | None = None
_memo: dict[str, list[float]] = {}  # per-process memo of API embeddings (bounded), so repeated lookups cost nothing


def _openai_embeddings():
    global _lc_embeddings, _lc_error
    s = get_settings()
    if _lc_embeddings is not None or _lc_error or not s.openai_api_key:
        return _lc_embeddings
    try:
        from langchain_openai import OpenAIEmbeddings  # type: ignore

        _lc_embeddings = OpenAIEmbeddings(
            model=s.embedding_model, api_key=s.openai_api_key, base_url=s.openai_base_url or None
        )
    except Exception as exc:
        _lc_error = f"{type(exc).__name__}: {exc}"
    return _lc_embeddings


def embedding_id() -> str:
    s = get_settings()
    return f"openai-{s.embedding_model}" if _openai_embeddings() is not None else f"hashing-{HASH_DIM}"


def embedding_error() -> str | None:
    return _lc_error


def _hash_embed(text: str) -> np.ndarray:
    v = np.zeros(HASH_DIM, dtype=np.float32)
    words = re.findall(r"[a-z0-9+#.]+", text.lower())
    feats: list[str] = []
    for w in words:
        feats.append("w:" + w)
        padded = f"^{w}$"
        feats.extend("c:" + padded[i : i + 3] for i in range(max(1, len(padded) - 2)))
    for f in feats:
        h = zlib.crc32(f.encode())
        v[h % HASH_DIM] += 1.0 if (h >> 16) & 1 else -1.0
    n = np.linalg.norm(v)
    return v / n if n else v


def embed_texts(texts: list[str]) -> np.ndarray:
    """Return an (n, d) array of L2-normalised vectors."""
    if not texts:
        return np.zeros((0, HASH_DIM), dtype=np.float32)
    emb = _openai_embeddings()
    if emb is None:
        return np.vstack([_hash_embed(t) for t in texts])

    ns = f"emb:{get_settings().embedding_model}"
    vecs: list[list[float] | None] = []
    for t in texts:
        v = _memo.get(t)
        vecs.append(v if v is not None else cache.exact_get(ns, t))
    missing = [i for i, v in enumerate(vecs) if v is None]
    if missing:
        fresh = emb.embed_documents([texts[i] for i in missing])
        for i, v in zip(missing, fresh):
            vecs[i] = v
            cache.exact_set(ns, texts[i], v)
    for t, v in zip(texts, vecs):
        if len(_memo) < 20000:
            _memo[t] = v
    arr = np.array(vecs, dtype=np.float32)
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    return arr / np.where(norms == 0, 1, norms)
