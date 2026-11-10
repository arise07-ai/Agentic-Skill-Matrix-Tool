"""NARRATIVES: short texts the model writes from VERIFIED facts (a staffing summary, a gap-forecast narrative, a seniority sentence).
The model only words the facts. This module checks the wording before anyone sees it, and a text that fails is dropped: the page then
shows the computed numbers without it.

A text is accepted only when ALL of these hold:
  - it is one short paragraph (no lists, headings, links or markdown) within the length limit;
  - every number in it is a number from the facts;
  - every employee name in it is a person named in the facts (never someone else from the company);
  - every skill name in it is a skill named in the facts (never another skill from the matrix).
"""
import re

MAX_CHARS = 700
_NUM = re.compile(r"(?<![\w.])\d+(?:\.\d+)?%?(?![\w])")
_MARKUP = re.compile(r"(https?://|www\.|[*_`#>|]|\n\s*[-•\d]+[.)]?\s)")


def numbers_in(obj) -> set[str]:
    """Every number that appears in the facts (as text, so 3 and 3.0 are both accepted when either is given)."""
    out: set[str] = set()

    def walk(x):
        if isinstance(x, bool) or x is None:
            return
        if isinstance(x, (int, float)):
            out.add(f"{x:g}")
            if float(x) == int(x):
                out.add(str(int(x)))
        elif isinstance(x, str):
            out.update(n.rstrip("%") for n in _NUM.findall(x))
        elif isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, (list, tuple, set)):
            for v in x:
                walk(v)

    walk(obj)
    return out


def _mentions(text: str, name: str) -> bool:
    if len(name) < 3:  # 'Go', 'R', 'C' would match ordinary words
        return False
    return re.search(r"(?<![\w+#])" + re.escape(name) + r"(?![\w+#])", text, re.I) is not None


def verify(text: str | None, facts: dict, *, all_names: list[str], all_skills: list[str], allowed_names: set[str],
           allowed_skills: set[str], extra_numbers: set[str] | None = None, limit: int = MAX_CHARS) -> tuple[str | None, str | None]:
    """-> (the text, None) when it passes, else (None, the reason). `all_*` = everyone in the matrix; `allowed_*` = those the facts name."""
    if not text or not text.strip():
        return None, "empty"
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        return None, f"longer than {limit} characters"
    if _MARKUP.search(text):
        return None, "contains a link, list or markdown"
    allowed_nums = numbers_in(facts) | (extra_numbers or set()) | {"0", "1", "2", "3", "4"}  # the level scale itself is always quotable
    extra = [n for n in _NUM.findall(text) if n.rstrip("%") not in allowed_nums]
    if extra:
        return None, f"numbers that are not in the facts: {', '.join(extra[:3])}"
    low_allowed_names = {n.lower() for n in allowed_names}
    foreign_people = [n for n in all_names if n.lower() not in low_allowed_names and _mentions(text, n)]
    if foreign_people:
        return None, f"names someone the facts do not mention: {foreign_people[0]}"
    low_allowed_skills = {s.lower() for s in allowed_skills}
    foreign_skills = [s for s in all_skills if s.lower() not in low_allowed_skills and _mentions(text, s)
                      and not any(a.lower() in s.lower() or s.lower() in a.lower() for a in allowed_skills if len(a) >= 3)]
    if foreign_skills:
        return None, f"names a skill the facts do not mention: {foreign_skills[0]}"
    return text, None
