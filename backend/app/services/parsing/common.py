"""Shared parsing logic for Excel tables and PDF tables/text.

Pipeline:  raw table -> find header row -> detect column mapping -> detect layout (wide/long)
           -> records [{employee_no, name, roles, background, skills:[{raw_skill, rating}]}]
"""
import re
from typing import Any

from rapidfuzz import fuzz

FIELDS = ["employee_no", "name", "roles", "skills", "ratings", "background", "domain_tags"]

SYNONYMS: dict[str, list[str]] = {
    "employee_no": ["employee number", "employee no", "employee no.", "emp no", "emp number", "emp id", "employee id",
                    "staff id", "staff no", "staff number", "id number", "empno", "employee code", "id"],
    "name": ["name", "employee name", "full name", "employee", "staff name"],
    "roles": ["roles", "role", "job title", "job role", "position", "title", "designation", "job roles"],
    "skills": ["skills", "skill", "skill name", "skill set", "skillset", "competencies", "competency", "technologies",
               "skills level", "skills levels", "skills rating", "skills ratings", "skills and levels",
               "skills and ratings", "skills with levels", "skills with ratings"],  # "Skills (level)" = inline "Python (4); SQL (3)"
    "ratings": ["skill ratings", "skill rating", "ratings", "rating", "proficiency", "proficiency level",
                "skill level", "level", "score", "skill score", "skill proficiency"],
    "background": ["background", "summary", "experience", "bio", "biography", "profile", "about", "education",
                   "work history", "professional background"],
    "domain_tags": ["domain tags", "domain expertise", "specialized domains", "technical domains"],
}

DELIM = re.compile(r"[,;\n|]+")
INLINE = re.compile(
    r"^(?P<s>.+?)\s*(?:\((?P<a>[^)]*)\)|[:=]\s*(?P<b>[\w.]+(?:\s*/\s*\d+)?)|\s[-–]\s*(?P<c>\d(?:\.\d)?))\s*$"
)
ENTITY = re.compile(r"&([A-Za-z]{1,10});")
OUT_OF = re.compile(r"^(\d(?:\.\d)?)\s*(?:/|out of)\s*(\d+)$")
# Skill levels are 0-4:  0 None, 1 Learned, 2 Applied, 3 Experienced, 4 Mastered.
SCALE_MAX = 4
OUT_OF_SCALE = "is outside the 0-4 scale"  # the marker parse_skills_cell looks for
LABELS = {
    "none": 0, "no skill": 0, "nil": 0,
    "learned": 1, "novice": 1, "beginner": 1, "basic": 1, "elementary": 1,
    "applied": 2, "intermediate": 2, "competent": 2,
    "experienced": 3, "proficient": 3, "advanced": 3,
    "mastered": 4, "expert": 4, "master": 4,
}


def norm(s: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(s).lower()).strip()


def clean(v: Any) -> str:
    if v is None:
        return ""
    s = str(v).strip()
    return "" if s.lower() in ("nan", "none", "nat") else s


def unwrap_cell(text: Any) -> str:
    """PDF table cells hard-wrap long text with newlines. Decide whether a newline is a line wrap
    (join with a space) or an intentional list separator (keep).
    Heuristic: if the cell already uses commas/semicolons as separators, every newline is a wrap;
    otherwise a newline is a separator unless the next line is clearly a continuation."""
    s = clean(text)
    if "\n" not in s:
        return s
    if "," in s or ";" in s:
        return re.sub(r"\s*\n\s*", " ", s)
    lines = [ln.strip() for ln in s.split("\n") if ln.strip()]
    out = lines[:1]
    for ln in lines[1:]:
        if ln[0] in "()" or ln[0].isdigit() or out[-1].endswith((":", "(", "-", "&", "/")):
            out[-1] += " " + ln
        else:
            out.append(ln)
    return "\n".join(out)


# --------------------------------------------------------------------------- mapping
def detect_mapping(columns: list[str]) -> dict[str, str]:
    normed = {c: norm(c) for c in columns}
    mapping: dict[str, str] = {}
    used: set[str] = set()
    for f in FIELDS:  # pass 1: exact synonym match
        syn = {norm(x) for x in SYNONYMS[f]}
        for c, n in normed.items():
            if c not in used and n in syn:
                mapping[f] = c
                used.add(c)
                break
    for f in FIELDS:  # pass 2: fuzzy for whatever is still unmapped
        if f in mapping:
            continue
        best, score = None, 0.0
        for c, n in normed.items():
            if c in used or not n:
                continue
            for s in SYNONYMS[f]:
                sc = fuzz.ratio(n, norm(s))
                if sc > score:
                    best, score = c, sc
        if best and score >= 85:
            mapping[f] = best
            used.add(best)
    return mapping


def find_header(raw_rows: list[list[Any]], search: int = 15) -> int | None:
    """Index of the header row: the first row (within `search`) mapping >=2 known fields."""
    for i, row in enumerate(raw_rows[:search]):
        cells = [clean(c) for c in row]
        if sum(1 for c in cells if c) < 2:
            continue
        m = detect_mapping([c for c in cells if c])
        if len(m) >= 2 and ("name" in m or "skills" in m):
            return i
    return None


def table_from_raw(raw_rows: list[list[Any]], header_idx: int) -> tuple[list[str], list[dict[str, str]]]:
    header = [re.sub(r"\s+", " ", clean(c)) or f"Column {j + 1}" for j, c in enumerate(raw_rows[header_idx])]
    seen: dict[str, int] = {}
    cols = []
    for h in header:  # de-duplicate column names
        seen[h] = seen.get(h, 0) + 1
        cols.append(h if seen[h] == 1 else f"{h} ({seen[h]})")
    rows = []
    for r in raw_rows[header_idx + 1 :]:
        d = {cols[j]: clean(r[j]) if j < len(r) else "" for j in range(len(cols))}
        if any(d.values()):
            rows.append(d)
    return cols, rows


# --------------------------------------------------------------------------- ratings / skills (levels 0-4)
def normalize_rating(val: Any) -> tuple[int | None, str | None]:
    if isinstance(val, str) and val.strip().lower() in ("none", "no skill", "nil"):  # the word None is the level 0, not an empty cell
        return 0, None
    s = clean(val).lower()
    if not s:
        return None, None
    m = OUT_OF.match(s)  # '3/4', '3 / 4', '3 out of 4': the number before the scale
    if m:
        if float(m.group(2)) != SCALE_MAX:  # '4/5' comes from the old 1-5 scale: do not guess what it means
            return None, f"Rating {val} is not on the 0-4 scale"
        s = m.group(1)
    try:
        f = float(s)
    except ValueError:
        if s in LABELS:
            return LABELS[s], None
        return None, f"Unrecognised rating '{val}'"
    if not 0 <= f <= SCALE_MAX:
        return None, f"Rating {val} {OUT_OF_SCALE}"
    r = int(f + 0.5)
    return r, (f"Rating {val} rounded to {r}" if f != r else None)


def split_list(text: str) -> list[str]:
    return [p.strip() for p in DELIM.split(text or "") if p.strip()]


def split_outside_parens(text: str) -> list[str]:
    """Like split_list, but a comma or semicolon inside brackets belongs to the name: 'Testing (NVDA, JAWS) (4)'."""
    parts, buf, depth = [], [], 0
    for ch in text or "":
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth = max(0, depth - 1)
        if depth == 0 and ch in ",;\n|":
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    parts.append("".join(buf))
    return [p.strip() for p in parts if p.strip()]


def parse_skills_cell(skills: str, ratings: str = "") -> tuple[list[dict], list[str]]:
    """Handles 'Python (4), SQL (3)', 'Python: 4; SQL: 3', 'Python, SQL' + '4, 3', and single skill+rating."""
    warns: list[str] = []
    items: list[dict] = []
    # 'ATT&CK;' is an HTML-entity artefact of the source file, not a list separator: 'MITRE ATT&CK;' -> 'MITRE ATT&CK'
    skills, ratings = ENTITY.sub(r"&\1", skills or ""), ENTITY.sub(r"&\1", ratings or "")
    for part in split_outside_parens(skills):
        m = INLINE.match(part)
        if m:
            rating, w = normalize_rating(m.group("a") or m.group("b") or m.group("c"))
            if rating is not None:
                items.append({"raw_skill": m.group("s").strip(), "rating": rating})
                if w:
                    warns.append(w)
                continue
            if w and (OUT_OF_SCALE in w or "not on the 0-4 scale" in w):  # a real number on the wrong scale: keep the skill, drop the rating
                items.append({"raw_skill": m.group("s").strip(), "rating": None})
                warns.append(f"{m.group('s').strip()}: {w}; the skill is listed without a rating")
                continue
        items.append({"raw_skill": part, "rating": None})

    # Ratings cell. Either one rating per skill in the same order ('4, 3'), or labelled 'Python: 4/4, SQL: 3/4' (any order):
    # labelled ratings are matched to the skill BY NAME, so a different order or a missing entry cannot shift them.
    rs = split_outside_parens(ratings)
    pending = [it for it in items if it["rating"] is None]
    labelled = [(a.strip(), b.strip()) for a, b in (r.rsplit(":", 1) for r in rs if ":" in r)] if rs else []
    if rs and len(labelled) == len(rs):
        by_name = {}
        for name, val in labelled:
            by_name.setdefault(name.lower(), val)
        for it in items:
            if it["rating"] is None and it["raw_skill"].lower() in by_name:
                it["rating"], w = normalize_rating(by_name[it["raw_skill"].lower()])
                if w:
                    warns.append(f"{it['raw_skill']}: {w}")
        unmatched = [n for n, _ in labelled if n.lower() not in {i["raw_skill"].lower() for i in items}]
        if unmatched:
            warns.append("Ratings given for skills that are not listed, ignored: " + ", ".join(unmatched[:5]))
    elif rs:
        if len(rs) == len(items):
            for it, r in zip(items, rs):
                if it["rating"] is None:
                    it["rating"], w = normalize_rating(r)
                    if w:
                        warns.append(f"{it['raw_skill']}: {w}")
        elif pending:
            warns.append(f"{len(items)} skills but {len(rs)} ratings; ratings left blank for review")
    return items, warns


# --------------------------------------------------------------------------- layout + records
def detect_layout(rows: list[dict[str, str]], mapping: dict[str, str]) -> str:
    """'long' = one row per employee-skill (keys repeat or are blank on continuation rows); else 'wide'."""
    keycol = mapping.get("employee_no") or mapping.get("name")
    if not keycol:
        return "wide"
    seen: set[str] = set()
    skillcol = mapping.get("skills")
    for i, r in enumerate(rows):
        k = r.get(keycol, "")
        if not k:
            if i > 0 and skillcol and r.get(skillcol):
                return "long"
            continue
        if k in seen:
            return "long"
        seen.add(k)
    return "wide"


def build_records(rows: list[dict[str, str]], mapping: dict[str, str], layout: str) -> tuple[list[dict], list[str]]:
    warnings: list[str] = []
    g = lambda r, f: r.get(mapping[f], "") if f in mapping else ""  # noqa: E731
    records: dict[str, dict] = {}
    last_key: str | None = None

    for n, r in enumerate(rows, start=2):
        no, name = g(r, "employee_no"), g(r, "name")
        key = no or name.lower()
        if not key:
            if layout == "long" and last_key:  # merged-cell / continuation row
                key = last_key
            elif any(r.values()):
                warnings.append(f"Row {n}: no name or employee number; skipped")
                continue
            else:
                continue
        last_key = key
        rec = records.setdefault(
            key, {"employee_no": no or None, "name": name, "roles": [], "background": "", "skills": [],
                  "domain_tags": [], "domain_tags_provided": "domain_tags" in mapping}
        )
        rec["employee_no"] = rec["employee_no"] or no or None
        rec["name"] = rec["name"] or name
        for role in split_list(g(r, "roles")):
            if role not in rec["roles"]:
                rec["roles"].append(role)
        for tag in split_list(g(r, "domain_tags")):
            if tag not in rec["domain_tags"]:
                rec["domain_tags"].append(tag)
        bg = g(r, "background")
        if bg and bg not in rec["background"]:
            rec["background"] = (rec["background"] + " " + bg).strip()
        skills, w = parse_skills_cell(g(r, "skills"), g(r, "ratings"))
        rec["skills"].extend(skills)
        warnings.extend(f"Row {n} ({rec['name'] or key}): {x}" for x in w)

    out = []
    for key, rec in records.items():
        if not rec["name"]:
            warnings.append(f"Employee '{key}' has no name; using the employee number as the name")
            rec["name"] = rec["employee_no"] or key
        if not rec["employee_no"]:
            warnings.append(f"{rec['name']}: no employee number; matched by name only")
        if not rec["skills"]:
            warnings.append(f"{rec['name']}: no skills found")
        out.append(rec)
    if "name" not in mapping and "employee_no" not in mapping:
        warnings.append("Neither Name nor Employee Number column was detected; map them manually")
    if "skills" not in mapping:
        warnings.append("No Skills column detected; map it manually")
    return out, warnings
