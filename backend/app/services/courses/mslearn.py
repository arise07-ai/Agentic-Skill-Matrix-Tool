"""Microsoft Learn Catalog API: free, no key, public. https://learn.microsoft.com/api/catalog/

The API has no free-text search, so the catalog of modules and learning paths is downloaded once, slimmed down, kept on disk
(COURSE_CACHE_HOURS, default 24) and searched here by whole words in the title, products, subjects and summary.
Field names (uid, title, url, duration_in_minutes, levels, products, subjects, summary, popularity, rating.average) are from
Microsoft's Catalog API developer reference. It covers Microsoft technologies only (Azure, Power BI, .NET, GitHub, SQL Server...).
"""
import html
import logging
import re

import httpx

from ...config import get_settings
from . import cache
from .. import groups
from .base import Course, SourceError, has_words, level_rank, mask, norm_level, words

log = logging.getLogger(__name__)
API = "https://learn.microsoft.com/api/catalog/"
CACHE = "mslearn_catalog.json"
_STOP = {"and", "for", "the", "of", "in", "to", "with", "development", "engineering", "skills"}


def _slim(raw: dict) -> list[dict]:
    out = []
    for kind, key in (("path", "learningPaths"), ("module", "modules")):
        for r in raw.get(key, []) or []:
            if not r.get("title") or not r.get("url"):
                continue
            out.append({"uid": r.get("uid", ""), "kind": kind, "title": r["title"], "url": r["url"],
                        "min": r.get("duration_in_minutes"), "levels": r.get("levels") or [],
                        "products": [p.replace("-", " ") for p in (r.get("products") or [])],
                        "subjects": [p.replace("-", " ") for p in (r.get("subjects") or [])],
                        "summary": re.sub(r"<[^>]+>", " ", html.unescape(r.get("summary") or ""))[:400],
                        "pop": r.get("popularity") or 0, "rating": (r.get("rating") or {}).get("average")})
    return out


def _catalog() -> list[dict]:
    s = get_settings()
    fresh = cache.read(CACHE, s.course_cache_hours * 3600)
    if fresh is not None:
        return fresh
    try:
        r = httpx.get(API, params={"type": "modules,learningPaths", "locale": s.mslearn_locale}, timeout=60, follow_redirects=True)
        r.raise_for_status()
        data = _slim(r.json())
        if not data:
            raise ValueError("empty catalog")
        cache.write(CACHE, data)
        return data
    except Exception as e:  # noqa: BLE001
        stale = cache.read(CACHE, allow_stale=True)
        if stale:
            log.warning("Microsoft Learn catalog refresh failed, using the older copy: %s", e)
            return stale
        raise SourceError(f"Microsoft Learn could not be reached ({type(e).__name__}: {mask(e)}).") from e


def search(skill: str, level: str | None = None, limit: int = 2) -> list[Course]:
    tokens = [t for t in words(skill) if t not in _STOP] or words(skill)
    if not tokens:
        return []
    scored = []
    tools = [tok for _, tok in groups.load().get(groups.match(skill) or "", [])]  # a broad skill: titles about one of its tools rank higher
    for r in _catalog():
        if has_words(r["title"], tokens):
            score = 3
        elif any(has_words(p, tokens) for p in r["products"]):
            score = 2
        elif has_words(" ".join(r["subjects"]), tokens) or has_words(r["summary"], tokens):
            score = 1
        else:
            continue
        lv = [norm_level(x) for x in r["levels"]] or ["all"]
        rank = min(level_rank(x, level) for x in lv)
        if tools and any(has_words(r["title"], list(t)) for t in tools):
            score += 2
        scored.append((-score, rank, 0 if r["kind"] == "path" else 1, -(r["pop"] or 0), r))
    scored.sort(key=lambda t: t[:4])
    out = []
    for *_, r in scored[:limit]:
        lv = [norm_level(x) for x in r["levels"]]
        out.append(Course(id=f"mslearn-{r['uid'] or r['url']}", title=r["title"], url=r["url"],
                          level=(level if level in lv else lv[0]) if lv else "all", skill=skill,
                          duration_hours=round(r["min"] / 60, 1) if r.get("min") else None,
                          rating=round(r["rating"], 1) if r.get("rating") else None, provider="Microsoft Learn", kind="course",
                          description=r.get("summary", "")[:1200]))
    return out
