"""Skill standardization: raw spelling -> canonical skill from the taxonomy.

Order (cheapest first):  manual override  ->  exact (name/alias)  ->  fuzzy (typos)  ->  semantic (embeddings)
                         ->  LLM judge (if configured)  ->  NEW skill, flagged for human review.
Anything that is not an exact/override match is flagged `needs_review` so a person confirms it on the
Standardization page; confirmations are stored as overrides and learned for future uploads.
"""
import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from rapidfuzz import fuzz

from .. import db
from ..ai import AINotConfigured, llm, tasks, vectorstore
from ..config import get_settings

log = logging.getLogger(__name__)
SEED = Path(__file__).resolve().parent.parent / "data" / "skill_taxonomy.json"
TAX_COLLECTION = "taxonomy"

_cache: tuple[dict, dict, dict] | None = None  # (taxonomy, lookup, lookup without punctuation)
_phrase_re: tuple[re.Pattern, dict] | None = None  # one pattern over every skill name and alias, for skills named inside a longer phrase


def _np(s: str) -> str:
    return re.sub(r"[^a-z0-9+#]", "", s.lower())


def invalidate() -> None:
    global _cache, _phrase_re
    _cache = None
    _phrase_re = None


def skills_named_in(raw: str, lookup: dict) -> list[str]:
    """Canonical skills whose name or alias appears in `raw` as whole words: "Spark job tuning" -> ["Apache Spark"].
    These are only EXTRA CANDIDATES for the model to judge, never an answer by themselves: "React Native mobile apps" contains React and
    "Java coffee roasting equipment" contains Java, and the model must still be free to say they are different things."""
    global _phrase_re
    if _phrase_re is None or _phrase_re[1] is not lookup:
        terms = sorted((t for t in lookup if len(t) >= 3), key=len, reverse=True)  # longest first; 1-2 letter terms (c, r, go) are too common
        pat = re.compile(r"(?<![a-z0-9+#])(" + "|".join(re.escape(t) for t in terms) + r")(?![a-z0-9+#])") if terms else None
        _phrase_re = (pat, lookup)
    pat = _phrase_re[0]
    if pat is None:
        return []
    found: list[str] = []
    for m in pat.finditer(raw.lower()):
        c = lookup[m.group(1)]
        if c not in found:
            found.append(c)
    return found


_category_sync_changed = False


def consume_category_sync_change() -> bool:
    """True once if the last taxonomy load changed the category of any bundled skill (employee vectors carry categories, so the
    caller rebuilds them); reading it clears it."""
    global _category_sync_changed
    changed, _category_sync_changed = _category_sync_changed, False
    return changed


def _seed_table() -> None:
    """Empty table: load the bundled taxonomy. Otherwise keep the table (people's additions and aliases stay) but make every bundled
    skill's CATEGORY follow skill_taxonomy.json, which owns it (the Taxonomy page refuses to change it)."""
    global _category_sync_changed
    data = json.loads(SEED.read_text(encoding="utf-8"))
    if not db.one("SELECT 1 AS x FROM taxonomy LIMIT 1"):
        with db.connect() as c:
            for name, meta in data.items():
                c.execute("INSERT OR IGNORE INTO taxonomy(name, category, aliases) VALUES (?,?,?)",
                          (name, meta.get("category", "Other"), json.dumps(meta.get("aliases", []))))
        return
    with db.connect() as c:
        for name, meta in data.items():
            cat = meta.get("category", "Other")
            cur = c.execute("UPDATE taxonomy SET category=? WHERE name=? AND category<>?", (cat, name, cat))
            if cur.rowcount:
                _category_sync_changed = True


def _load() -> tuple[dict, dict, dict]:
    global _cache
    if _cache is None:
        _seed_table()
        tax, lookup, loose = {}, {}, {}
        for r in db.rows("SELECT name, category, aliases FROM taxonomy"):
            aliases = json.loads(r["aliases"] or "[]")
            tax[r["name"]] = {"category": r["category"], "aliases": aliases}
            for term in [r["name"], *aliases]:
                lookup.setdefault(term.lower(), r["name"])
                loose.setdefault(_np(term), r["name"])
        _cache = (tax, lookup, loose)
    return _cache


def index() -> tuple[dict, dict]:
    """(taxonomy {name: {category, aliases}}, lookup {lowercase name/alias: canonical})"""
    tax, lookup, _ = _load()
    return tax, lookup


def ensure_taxonomy_indexed(force: bool = False) -> None:
    """Seed the taxonomy table and (re)build its vectors. Vector problems never stop the app."""
    tax, _ = index()
    try:
        if force or vectorstore.count(TAX_COLLECTION) < len(tax):
            names = sorted(tax)
            vectorstore.upsert(TAX_COLLECTION, names, names, [{"name": n, "category": tax[n]["category"]} for n in names])
    except Exception as e:  # noqa: BLE001
        log.warning("Taxonomy vector indexing failed (semantic matching disabled until fixed): %s", e)


def _tidy(raw: str) -> str:
    s = re.sub(r"\s+", " ", raw.strip())
    if s and s == s.lower():
        s = s.upper() if len(s) <= 3 else s.title()
    return s


def normalize(raw: str) -> dict:
    """-> {canonical, category, method, confidence, needs_review}"""
    s = get_settings()
    tax, lookup, loose = _load()
    raw = re.sub(r"\s+", " ", raw.strip())
    key = raw.lower()

    def hit(canonical: str, method: str, conf: float, review: bool) -> dict:
        return {"canonical": canonical, "category": tax.get(canonical, {}).get("category", "Other"),
                "method": method, "confidence": round(conf, 3), "needs_review": review}

    ov = db.one("SELECT canonical, category FROM mapping_overrides WHERE raw_key=?", (key,))
    if ov:
        return {"canonical": ov["canonical"], "category": ov["category"] or tax.get(ov["canonical"], {}).get("category", "Other"),
                "method": "manual", "confidence": 1.0, "needs_review": False}

    if key in lookup:
        return hit(lookup[key], "exact", 1.0, False)
    if _np(raw) in loose and _np(raw):
        return hit(loose[_np(raw)], "exact", 1.0, False)

    if len(key) >= 4:  # fuzzy: typos only; full-string ratio so "java" never matches "javascript"
        best, score = None, 0.0
        for term, canonical in lookup.items():
            if len(term) >= 4:
                sc = fuzz.ratio(key, term)
                if sc > score:
                    best, score = canonical, sc
        if best and score >= s.standardize_fuzzy_threshold:
            return hit(best, "fuzzy", score / 100, score < 95)

    top: list[tuple[str, float]] = []
    try:
        top = [(i, sim) for i, sim, _ in vectorstore.query(TAX_COLLECTION, raw, 5)]
    except Exception as e:  # noqa: BLE001
        log.debug("semantic lookup unavailable: %s", e)
    named = skills_named_in(raw, lookup)  # a skill spelled out inside a longer phrase: always put it in front of the model
    if top or named:
        best = top[0][1] if top else 0.0
        try:  # LLM judge when configured, for plausible candidates
            if named or best >= s.standardize_model_min_similarity:
                candidates = list(dict.fromkeys([*named[:3], *[n for n, _ in top]]))[:7]
                verdict = tasks.match_skills(raw, candidates)
                if verdict["canonical"]:
                    return hit(verdict["canonical"], verdict.get("method", "llm"), verdict["confidence"], verdict["confidence"] < 0.9)
        except AINotConfigured:
            if top and best >= s.standardize_semantic_threshold:
                return hit(top[0][0], "semantic", top[0][1], True)
        except Exception as e:  # noqa: BLE001
            log.warning("LLM skill matching failed: %s", e)
            if top and best >= s.standardize_semantic_threshold:
                return hit(top[0][0], "semantic", top[0][1], True)

    return {"canonical": _tidy(raw), "category": "Other", "method": "new", "confidence": 0.0, "needs_review": True}


def normalize_many(raws: list[str], workers: int = 8) -> dict[str, dict]:
    """normalize() for many spellings at once (used by imports). Embeds them in one batched call first, then
    resolves them in parallel, so 400+ new skills do not become 400+ sequential API round trips."""
    unique = list(dict.fromkeys(raws))
    if not unique:
        return {}
    try:
        from ..ai.embeddings import embed_texts

        embed_texts(unique)  # warms the embedding memo; vectorstore.query() then finds every text already embedded
    except Exception as e:  # noqa: BLE001  normalize() copes without vectors
        log.warning("Batch embedding failed, falling back to one-by-one lookups: %s", e)
    _load()  # build the taxonomy cache once, before the threads start
    if not llm.is_configured():  # no network calls to overlap; threads would only add overhead
        workers = 1
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        return dict(zip(unique, pool.map(normalize, unique)))


def apply_override(raw: str, canonical: str, category: str | None = None) -> int:
    """Human decision: every spelling `raw` means `canonical`. Learned for future uploads. Returns rows updated."""
    raw, canonical = re.sub(r"\s+", " ", raw.strip()), re.sub(r"\s+", " ", canonical.strip())
    if not raw or not canonical:
        raise ValueError("raw_skill and canonical must not be empty")
    tax, _, _ = _load()
    cat = category or tax.get(canonical, {}).get("category") or "Other"
    new_taxonomy_entry = canonical not in tax
    with db.connect() as c:
        c.execute("INSERT INTO mapping_overrides(raw_key, canonical, category) VALUES (?,?,?) "
                  "ON CONFLICT(raw_key) DO UPDATE SET canonical=excluded.canonical, category=excluded.category",
                  (raw.lower(), canonical, cat))
        if new_taxonomy_entry:
            c.execute("INSERT INTO taxonomy(name, category, aliases) VALUES (?,?,?)",
                      (canonical, cat, json.dumps([] if raw.lower() == canonical.lower() else [raw])))
        cur = c.execute("UPDATE employee_skills SET canonical_skill=?, category=?, method='manual', confidence=1.0, needs_review=0 "
                        "WHERE lower(raw_skill)=lower(?)", (canonical, cat, raw))
        changed = cur.rowcount
        # one employee may now hold the same canonical skill twice: keep the highest rating
        dups = c.execute("SELECT employee_id, canonical_skill FROM employee_skills WHERE canonical_skill=? "
                         "GROUP BY employee_id HAVING COUNT(*)>1", (canonical,)).fetchall()
        for d in dups:
            ids = [r["id"] for r in c.execute(
                "SELECT id FROM employee_skills WHERE employee_id=? AND canonical_skill=? "
                "ORDER BY rating IS NULL, rating DESC, id", (d["employee_id"], canonical))]
            c.executemany("DELETE FROM employee_skills WHERE id=?", [(i,) for i in ids[1:]])
    invalidate()
    if new_taxonomy_entry:
        ensure_taxonomy_indexed(force=True)
    return changed
