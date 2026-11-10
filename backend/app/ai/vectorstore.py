"""VECTOR DB (Chroma): a persistent folder, or a Chroma server when CHROMA_HOST is set.

The functions here form the retrieval/indexing interface for the rest of the app. To use Qdrant/pgvector,
re-implement that interface with the same signatures.

Vectors come from ai/embeddings.py and are passed to Chroma explicitly. Collections are named
"<collection>__<embedding id>", so switching the embedding model never mixes dimensions (then re-index).
Falls back to an in-memory store if `chromadb` is not installed (data is then lost on restart).
"""
import logging
import re
import threading

import numpy as np

from ..config import get_settings
from .embeddings import embed_texts, embedding_id
from . import reranker

log = logging.getLogger(__name__)
_client = None
_kind = ""
_error: str | None = None
_mem: dict[str, dict] = {}
_lock = threading.Lock()


class _MemCol:
    def __init__(self) -> None:
        self.d: dict[str, tuple] = {}

    def count(self) -> int:
        return len(self.d)

    def get(self, ids=None, where=None, include=None):
        keys = [
            i for i in (ids if ids is not None else self.d)
            if i in self.d and _matches(self.d[i][2], where)
        ]
        out = {"ids": keys, "embeddings": None, "documents": None, "metadatas": None}
        if include and "embeddings" in include:
            out["embeddings"] = [self.d[k][0] for k in keys]
        if include and "documents" in include:
            out["documents"] = [self.d[k][1] for k in keys]
        if include and "metadatas" in include:
            out["metadatas"] = [self.d[k][2] for k in keys]
        return out

    def upsert(self, ids, documents, embeddings, metadatas):
        for i, doc, e, m in zip(ids, documents, embeddings, metadatas):
            self.d[i] = (e, doc, m)

    def delete(self, ids=None, where=None):
        keys = ids if ids is not None else [
            i for i, (_, _, meta) in self.d.items() if _matches(meta, where)
        ]
        for i in keys:
            self.d.pop(i, None)

    def query(self, query_embeddings, n_results, include, where=None):
        q = np.asarray(query_embeddings[0], dtype=np.float32)
        q = q / (np.linalg.norm(q) or 1)
        res = []
        for i, (e, doc, m) in self.d.items():
            if not _matches(m, where):
                continue
            v = np.asarray(e, dtype=np.float32)
            v = v / (np.linalg.norm(v) or 1)
            res.append((1 - float(q @ v), i, doc, m))
        res.sort(key=lambda x: x[0])
        res = res[:n_results]
        out = {
            "ids": [[r[1] for r in res]],
            "distances": [[r[0] for r in res]],
            "metadatas": [[r[3] for r in res]],
            "documents": [[r[2] for r in res]],
        }
        return out


def _matches(metadata: dict, where: dict | None) -> bool:
    if not where:
        return True
    if "$and" in where:
        return all(_matches(metadata, clause) for clause in where["$and"])
    if "$or" in where:
        return any(_matches(metadata, clause) for clause in where["$or"])
    for key, expected in where.items():
        actual = metadata.get(key)
        if isinstance(expected, dict):
            if "$in" in expected and actual not in expected["$in"]:
                return False
            if "$eq" in expected and actual != expected["$eq"]:
                return False
        elif actual != expected:
            return False
    return True


def _chroma_where(where: dict | None) -> dict | None:
    if not where or len(where) == 1:
        return where
    return {"$and": [{key: value} for key, value in where.items()]}


def _get_client():
    with _lock:  # the background warm-up and the first request must not build two clients
        return _build_client()


def _build_client():
    global _client, _kind, _error
    if _client is not None:
        return _client
    s = get_settings()
    try:
        import chromadb  # type: ignore

        if s.chroma_host:
            _client = chromadb.HttpClient(host=s.chroma_host, port=s.chroma_port)
            _kind = f"chroma server {s.chroma_host}:{s.chroma_port}"
        else:
            _client = chromadb.PersistentClient(path=s.chroma_dir)
            _kind = f"chroma persistent ({s.chroma_dir})"
    except Exception as exc:  # not installed, or server unreachable
        _error = f"{type(exc).__name__}: {exc}"
        log.warning("Chroma unavailable (%s); using in-memory vector store.", _error)

        class _MemClient:
            def get_or_create_collection(self, name, metadata=None, embedding_function=None):
                return _mem.setdefault(name, _MemCol())

        _client, _kind = _MemClient(), "in-memory fallback (chromadb unavailable)"
    return _client


def _col(collection: str):
    name = re.sub(r"[^a-zA-Z0-9_-]", "_", f"{collection}__{embedding_id()}")[:60].strip("_-") or "c"
    return _get_client().get_or_create_collection(name=name, metadata={"hnsw:space": "cosine"})


def upsert(collection: str, ids: list[str], docs: list[str], metas: list[dict]) -> None:
    if not ids:
        return
    _col(collection).upsert(
        ids=ids, documents=docs, embeddings=[list(map(float, v)) for v in embed_texts(docs)], metadatas=metas
    )


def replace_where(collection: str, where: dict | None, ids: list[str], docs: list[str], metas: list[dict]) -> None:
    """Embed the replacement documents before removing matching existing records."""
    if not (len(ids) == len(docs) == len(metas)):
        raise ValueError("ids, docs, and metas must have matching lengths.")
    vectors = embed_texts(docs) if docs else []
    col = _col(collection)
    if where:
        col.delete(where=_chroma_where(where))
    else:
        existing_ids = col.get(include=[]).get("ids", [])
        if existing_ids:
            col.delete(ids=existing_ids)
    if ids:
        col.upsert(
            ids=ids, documents=docs, embeddings=[list(map(float, v)) for v in vectors], metadatas=metas
        )


def query_with_documents(collection: str, text: str, n: int, where: dict | None = None) -> list[dict]:
    """Return vector matches with their document text and metadata; `where` uses Chroma metadata filters."""
    if n <= 0:
        return []
    col = _col(collection)
    total = col.count()
    if total == 0:
        return []
    q = embed_texts([text])[0]
    res = col.query(
        query_embeddings=[list(map(float, q))],
        n_results=min(n, total),
        include=["documents", "metadatas", "distances"],
        **({"where": _chroma_where(where)} if where else {}),
    )
    return [
        {"id": i, "similarity": 1.0 - float(d), "document": doc, "metadata": meta or {}}
        for i, d, doc, meta in zip(
            res["ids"][0], res["distances"][0], res["documents"][0], res["metadatas"][0]
        )
    ]


def query(collection: str, text: str, n: int) -> list[tuple[str, float, dict]]:
    """Return [(id, cosine similarity, metadata)] best first."""
    return [
        (hit["id"], hit["similarity"], hit["metadata"])
        for hit in query_with_documents(collection, text, n)
    ]


def reranked_query(collection: str, text: str, n: int, where: dict | None = None) -> list[tuple[str, float, dict]]:
    """Search vectors, rerank locally, and retain cosine scores; log and keep vector order if reranking fails."""
    hits = query_with_documents(collection, text, n, where)
    try:
        ranked = reranker.rerank(text, hits)
    except Exception as exc:  # noqa: BLE001  vector results remain usable without the cross-encoder
        log.warning("FlashRank unavailable; retaining vector similarity ordering: %s", exc)
        ranked = hits
    return [(hit["id"], hit["similarity"], hit["metadata"]) for hit in ranked]


def get_documents(collection: str, ids: list[str]) -> dict[str, dict]:
    """Fetch stored documents and metadata by vector id."""
    if not ids:
        return {}
    found = _col(collection).get(ids=ids, include=["documents", "metadatas"])
    return {
        i: {"id": i, "document": doc, "metadata": meta or {}}
        for i, doc, meta in zip(found["ids"], found["documents"], found["metadatas"])
    }


def get_embeddings(collection: str, texts: list[str]) -> dict[str, list[float]]:
    """Vectors for `texts` (ids == the texts). Missing ones are embedded and stored."""
    texts = list(dict.fromkeys(texts))
    if not texts:
        return {}
    col = _col(collection)
    got = col.get(ids=texts, include=["embeddings"])
    have: dict[str, list[float]] = {}
    if got.get("embeddings") is not None:
        have = {i: [float(x) for x in e] for i, e in zip(got["ids"], got["embeddings"])}
    missing = [t for t in texts if t not in have]
    if missing:
        vecs = embed_texts(missing)
        col.upsert(
            ids=missing, documents=missing, embeddings=[list(map(float, v)) for v in vecs],
            metadatas=[{"kind": collection} for _ in missing],
        )
        have.update({t: [float(x) for x in v] for t, v in zip(missing, vecs)})
    return have


def delete(collection: str, ids: list[str]) -> None:
    if ids:
        _col(collection).delete(ids=ids)


def delete_where(collection: str, where: dict) -> None:
    _col(collection).delete(where=_chroma_where(where))


def count_where(collection: str, where: dict) -> int:
    return len(_col(collection).get(where=_chroma_where(where), include=["metadatas"]).get("ids", []))


def reset(collection: str) -> None:
    col = _col(collection)
    ids = col.get()["ids"]
    if ids:
        col.delete(ids=ids)


def count(collection: str) -> int:
    return _col(collection).count()


def store_status() -> dict:
    _get_client()
    return {"backend": _kind, "error": _error, "persistent": _kind.startswith("chroma")}
