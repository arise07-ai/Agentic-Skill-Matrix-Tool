"""Learning content for the study plans: one `Course` shape for every source.

A source is one of: catalog (your own CSV), mslearn (Microsoft Learn Catalog API), youtube (YouTube Data API), udemy (optional, old adapter).
`kind` says what the person gets: "course" (structured path or module), "video" (a YouTube video) or "docs" (a reading list or reference).
"""
import re

from pydantic import BaseModel


class Course(BaseModel):
    id: str
    title: str
    url: str
    level: str = "all"  # beginner | intermediate | expert | all
    skill: str
    duration_hours: float | None = None
    rating: float | None = None
    is_sample: bool = False
    provider: str = ""
    kind: str = "course"  # course | video | docs
    description: str = ""
    learning_outcomes: str = ""


class SourceError(Exception):
    """A source could not be used. The message is shown to the user as a warning, so it must hold no secret."""


def words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9+#]+", (text or "").lower().replace("&", " and "))


def has_words(text: str, tokens: list[str]) -> bool:
    """True when every token occurs in the text as a whole word (so 'java' does not match 'javascript')."""
    have = set(words(text))
    return bool(tokens) and all(t in have for t in tokens)


def norm_level(value: str | None) -> str:
    v = (value or "").strip().lower()
    if v in ("advanced", "expert", "senior"):
        return "expert"
    if v in ("beginner", "intro", "introductory", "basic"):
        return "beginner"
    if v in ("intermediate", "mid"):
        return "intermediate"
    return "all"


_ORDER = {"beginner": 1, "intermediate": 2, "expert": 3}


def level_rank(course_level: str, wanted: str | None) -> int:
    """Sort key for 'how well does this fit the wanted level': 0 exact, 1 any level, 2 easier (a good start), 3 harder."""
    if not wanted or course_level == wanted:
        return 0
    if course_level == "all" or course_level not in _ORDER or wanted not in _ORDER:
        return 1
    return 2 if _ORDER[course_level] < _ORDER[wanted] else 3


def mask(text: str, *secrets: str) -> str:
    """Remove secret values and key-like strings from an error message."""
    out = str(text)
    for s in secrets:
        if s:
            out = out.replace(s, "***")
    return re.sub(r"(AIza|sk-|tvly-)[A-Za-z0-9_\-]{6,}", r"\1***", out)[:160]


_FILLER = {"and", "for", "the", "of", "in", "to", "with", "development", "engineering", "skills", "tutorial", "basics", "real", "time"}


def stems(text: str) -> set[str]:
    """First five letters of each meaningful word, so 'Transactions' and 'transaction' count as the same word."""
    return {w[:5] for w in words(text) if len(w) >= 3 and w not in _FILLER}


def is_about(title: str, skill: str, extra_terms: list[str] | None = None) -> bool:
    """True when a title shares at least one meaningful word with the skill (or with one of its tools). Keeps off-topic videos out."""
    want = stems(skill)
    for term in extra_terms or []:
        want |= stems(term)
    return not want or bool(want & stems(title))
