"""Local cross-encoder reranking for vector-search candidates."""
from __future__ import annotations

import math
import threading
from typing import Any

_ranker = None
_error: str | None = None
_initialization_lock = threading.Lock()
_inference_lock = threading.Lock()
MODEL = "ms-marco-MiniLM-L-12-v2"


def _get_ranker():
    global _ranker, _error
    if _ranker is not None:
        return _ranker
    with _initialization_lock:
        if _ranker is not None:
            return _ranker
        if _error:
            raise RuntimeError(f"FlashRank initialization failed: {_error}")
        try:
            from flashrank import Ranker  # type: ignore

            _ranker = Ranker(model_name=MODEL)
            return _ranker
        except Exception as exc:
            _error = f"{type(exc).__name__}: {exc}"
            raise RuntimeError(f"Could not initialize local FlashRank model {MODEL}: {_error}") from exc


def rerank(query: str, passages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return passages ordered by cross-encoder relevance, preserving their vector scores."""
    if not passages:
        return []
    ranker = _get_ranker()
    from flashrank import RerankRequest  # type: ignore

    request = RerankRequest(
        query=query,
        passages=[
            {"id": str(p["id"]), "text": str(p["document"]), "meta": p.get("metadata") or {}}
            for p in passages
        ],
    )
    with _inference_lock:
        results = ranker.rerank(request)
    by_id = {str(p["id"]): p for p in passages}
    ranked = []
    for result in results:
        passage = by_id.get(str(result.get("id")))
        try:
            score = float(result["score"])
        except (KeyError, TypeError, ValueError):
            continue
        if passage is None or not math.isfinite(score):
            continue
        ranked.append({**passage, "rerank_score": max(0.0, min(1.0, score))})
    if len(ranked) != len(passages):
        raise RuntimeError(f"FlashRank returned {len(ranked)} of {len(passages)} passages.")
    return ranked


def status() -> dict[str, str]:
    if _error:
        return {"state": "error", "model": MODEL, "detail": _error}
    if _ranker is not None:
        return {"state": "loaded", "model": MODEL, "detail": "Local cross-encoder is ready."}
    try:
        import importlib.util

        if importlib.util.find_spec("flashrank") is None:
            return {"state": "unavailable", "model": MODEL, "detail": "Install the backend FlashRank dependency."}
    except (ImportError, ValueError):
        return {"state": "unavailable", "model": MODEL, "detail": "FlashRank is unavailable."}
    return {"state": "ready on first search", "model": MODEL, "detail": "Local model loads on first reranked search."}
