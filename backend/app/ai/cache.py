"""Two-layer cache for AI calls.

  Layer 1  Redis      exact-match (sha256 of the input). Shared across workers. Used for embeddings and LLM calls.
                      Falls back to a per-process in-memory dict when REDIS_URL is not set / unreachable.
  Layer 2  GPTCache   *semantic* match (similar prompts hit). Opt-in: GPTCACHE_ENABLED=true and only for the
                      namespaces in GPTCACHE_NAMESPACES (default: "requirements").

  !! Semantic caches can return a cached answer for a *similar but different* prompt
     ("need Python level 2" vs "level 4"). Keep GPTCACHE_NAMESPACES small and the similarity high.

"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any

from ..config import get_settings

_mem: dict[str, tuple[float, str]] = {}
_redis = None
_redis_error: str | None = None
_gpt = None
_gpt_error: str | None = None
_gpt_operation_error: str | None = None
_gpt_operational = False
logger = logging.getLogger(__name__)


def _key(ns: str, text: str) -> str:
    return f"skillmatrix:{ns}:{hashlib.sha256(text.encode('utf-8')).hexdigest()}"


def _get_redis():
    global _redis, _redis_error
    s = get_settings()
    if _redis is not None or not s.redis_url or _redis_error:
        return _redis
    try:
        import redis  # type: ignore

        client = redis.Redis.from_url(s.redis_url, decode_responses=True, socket_timeout=2, socket_connect_timeout=2)
        client.ping()
        _redis = client
    except Exception as exc:  # missing package or server down -> in-memory fallback
        _redis_error = f"{type(exc).__name__}: {exc}"
    return _redis


def exact_get(ns: str, text: str) -> Any | None:
    k = _key(ns, text)
    r = _get_redis()
    raw = None
    if r is not None:
        try:
            raw = r.get(k)
        except Exception:
            raw = None
    else:
        hit = _mem.get(k)
        if hit and hit[0] > time.time():
            raw = hit[1]
    return json.loads(raw) if raw is not None else None


def exact_set(ns: str, text: str, value: Any, ttl: int | None = None) -> None:
    ttl = ttl or get_settings().cache_ttl_seconds
    k, raw = _key(ns, text), json.dumps(value)
    r = _get_redis()
    if r is not None:
        try:
            r.set(k, raw, ex=ttl)
            return
        except Exception:
            pass
    _mem[k] = (time.time() + ttl, raw)


# ------------------------------------------------------------------ semantic layer
def _gptcache_embedding():
    import numpy as np
    import onnxruntime  # type: ignore
    from huggingface_hub import hf_hub_download  # type: ignore
    from tokenizers import Tokenizer  # type: ignore

    class CompatibleOnnx:
        def __init__(self) -> None:
            # Avoid AutoTokenizer: importing Transformers also loads PyTorch's conflicting OpenMP runtime.
            tokenizer_repo = "GPTCache/paraphrase-albert-small-v2"
            self.tokenizer = Tokenizer.from_file(
                hf_hub_download(repo_id=tokenizer_repo, filename="tokenizer.json")
            )
            self.tokenizer.enable_truncation(max_length=512)
            self.tokenizer.enable_padding(
                length=512,
                pad_id=0,
                pad_token="<pad>",
                direction="right",
                pad_type_id=0,
            )
            config_path = hf_hub_download(repo_id=tokenizer_repo, filename="config.json")
            with open(config_path, encoding="utf-8") as config_file:
                self._dimension = int(json.load(config_file)["hidden_size"])
            model_path = hf_hub_download(
                repo_id="GPTCache/paraphrase-albert-onnx",
                filename="model.onnx",
            )
            self.ort_session = onnxruntime.InferenceSession(model_path)

        @property
        def dimension(self) -> int:
            return self._dimension

        def to_embeddings(self, data, **_):
            encoded = self.tokenizer.encode(data)
            ort_inputs = {
                "input_ids": np.asarray(encoded.ids, dtype="int64").reshape(1, -1),
                "attention_mask": np.asarray(encoded.attention_mask, dtype="int64").reshape(1, -1),
                "token_type_ids": np.asarray(encoded.type_ids, dtype="int64").reshape(1, -1),
            }
            token_embeddings = self.ort_session.run(None, ort_inputs)[0]
            mask = np.expand_dims(ort_inputs["attention_mask"], -1).repeat(
                token_embeddings.shape[-1], -1
            ).astype(float)
            pooled = np.sum(token_embeddings * mask, axis=1) / np.maximum(
                mask.sum(axis=1), 1e-9
            )
            return pooled.flatten()

    return CompatibleOnnx()


def _get_gptcache():
    global _gpt, _gpt_error
    s = get_settings()
    if _gpt is not None or not s.gptcache_enabled or _gpt_error:
        return _gpt
    try:
        from gptcache import Cache, Config  # type: ignore
        from gptcache.adapter.api import init_similar_cache  # type: ignore

        c = Cache()
        embedding = _gptcache_embedding()
        init_similar_cache(
            data_dir=str(s.data_path / "gptcache"),
            cache_obj=c,
            embedding=embedding,
            config=Config(similarity_threshold=s.gptcache_similarity),
        )
        _gpt = c
    except Exception as exc:
        _gpt_error = f"{type(exc).__name__}: {exc}"
    return _gpt


def semantic_get(ns: str, text: str) -> Any | None:
    global _gpt_operational, _gpt_operation_error
    if ns not in get_settings().gptcache_ns:
        return None
    c = _get_gptcache()
    if c is None:
        return None
    try:
        from gptcache.adapter.api import get  # type: ignore

        raw = get(f"{ns}: {text}", cache_obj=c)
        _gpt_operational = True
        _gpt_operation_error = None
        return json.loads(raw) if raw else None
    except Exception as exc:
        _gpt_operation_error = f"{type(exc).__name__}: {exc}"
        logger.warning("GPTCache semantic lookup failed: %s", _gpt_operation_error)
        return None


def semantic_set(ns: str, text: str, value: Any) -> None:
    global _gpt_operational, _gpt_operation_error
    if ns not in get_settings().gptcache_ns:
        return
    c = _get_gptcache()
    if c is None:
        return
    try:
        from gptcache.adapter.api import put  # type: ignore

        put(f"{ns}: {text}", json.dumps(value), cache_obj=c)
        _gpt_operational = True
        _gpt_operation_error = None
    except Exception as exc:
        _gpt_operation_error = f"{type(exc).__name__}: {exc}"
        logger.warning("GPTCache semantic write failed: %s", _gpt_operation_error)


# ------------------------------------------------------------------ combined helpers
def cache_get(ns: str, text: str) -> Any | None:
    hit = exact_get(ns, text)
    return hit if hit is not None else semantic_get(ns, text)


def cache_set(ns: str, text: str, value: Any) -> None:
    exact_set(ns, text, value)
    semantic_set(ns, text, value)


def cache_status() -> dict:
    s = get_settings()
    r = _get_redis() if s.redis_url else None
    exact_cache = "Redis shared" if r is not None else "in-memory fallback"
    if not s.gptcache_enabled:
        semantic_cache = "disabled"
    elif _gpt is not None:
        if _gpt_operation_error:
            semantic_cache = "error: " + _gpt_operation_error
        elif _gpt_operational:
            semantic_cache = "operational"
        else:
            semantic_cache = "initialized (awaiting first operation)"
    elif _gpt_error:
        semantic_cache = "error: " + _gpt_error
    else:
        semantic_cache = "enabled (initializes on first use)"
    return {
        "redis": "active" if r is not None else ("error: " + _redis_error if _redis_error else "not configured (in-memory fallback)"),
        "exact_cache": exact_cache,
        "gptcache": semantic_cache,
    }
