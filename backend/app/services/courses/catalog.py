"""Your own curated catalog: a CSV file you edit (backend/app/data/course_catalog.csv, or COURSE_CATALOG_PATH).

Columns (header row required; only skill, title and url are needed):
  skill, title, url, provider, level, duration_hours, type, rating, description, learning_outcomes
  - skill:  a skill name from your taxonomy ("Python"), or a broad skill from skill_groups.json ("Cloud")
  - level:  beginner | intermediate | advanced (or expert) | all
  - type:   course | video | docs   (default course)
Rows are matched to a skill by name (case-insensitive). A broad skill ("Cloud") also gets the rows of its tools ("AWS", "Terraform"...).
Nothing here is invented: the app only shows rows you wrote. Rows with a missing title or a URL that is not http(s) are skipped.
"""
import csv
import logging
from functools import lru_cache

from ...config import get_settings
from .. import groups
from .base import Course, SourceError, has_words, level_rank, norm_level, words

log = logging.getLogger(__name__)
HEADER = ["skill", "title", "url", "provider", "level", "duration_hours", "type", "rating"]


def _num(v: str | None) -> float | None:
    try:
        return float(v) if v not in (None, "") else None
    except ValueError:
        return None


@lru_cache(maxsize=4)
def _load(path: str, mtime: float) -> tuple[list[Course], int]:
    rows, skipped = [], 0
    with open(path, newline="", encoding="utf-8-sig") as f:
        for i, r in enumerate(csv.DictReader(f), start=2):
            r = {(k or "").strip().lower(): (v or "").strip() for k, v in r.items()}
            if not r.get("skill") and not r.get("title") and not r.get("url"):
                continue  # empty line
            if not r.get("skill") or not r.get("title") or not r.get("url", "").lower().startswith(("http://", "https://")):
                skipped += 1
                log.warning("course catalog line %s skipped (needs skill, title and an http(s) url)", i)
                continue
            kind = r.get("type", "course").lower()
            rows.append(Course(id=f"catalog-{i}", title=r["title"], url=r["url"], level=norm_level(r.get("level")), skill=r["skill"],
                               duration_hours=_num(r.get("duration_hours")), rating=_num(r.get("rating")),
                               provider=r.get("provider") or "Catalog", kind=kind if kind in ("course", "video", "docs") else "course",
                               description=r.get("description", "")[:1200],
                               learning_outcomes=r.get("learning_outcomes", "")[:1200]))
    return rows, skipped


def load() -> tuple[list[Course], int]:
    """(rows, skipped line count). A missing file is an empty catalog, a broken file raises SourceError."""
    p = get_settings().catalog_file
    if not p.exists():
        return [], 0
    try:
        return _load(str(p), p.stat().st_mtime)
    except (OSError, UnicodeDecodeError, csv.Error) as e:
        raise SourceError(f"The course catalog file could not be read ({type(e).__name__}).") from e


def search(skill: str, level: str | None = None, limit: int = 3) -> list[Course]:
    rows, _ = load()
    if not rows:
        return []
    want = skill.strip().lower()
    group = groups.match(skill)
    tools = [tok for _, tok in groups.load().get(group, [])] if group else []
    qtok = words(skill)
    scored: list[tuple[int, int, Course]] = []
    for c in rows:
        s = c.skill.lower()
        if s == want:
            tier = 0
        elif tools and any(tuple(words(c.skill)[: len(t)]) == t for t in tools):
            tier = 1  # a tool of the broad skill
        elif qtok and has_words(c.skill, qtok):
            tier = 2  # the skill name appears as whole words ("Python" in "Python for data")
        else:
            continue
        scored.append((tier, level_rank(c.level, level), c))
    scored.sort(key=lambda t: (t[0], t[1], -(t[2].rating or 0)))
    return [c.model_copy(update={"skill": skill}) for _, _, c in scored[:limit]]
