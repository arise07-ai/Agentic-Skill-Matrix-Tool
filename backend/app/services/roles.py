"""Roles: one spelling per role, and a CODE check of the AI's role-vs-skill proposals.

The AI proposes (ai/tasks.match_roles); this file verifies; a person approves (agents/proposals.py, kind `employee_roles`).

What code decides, never the AI:
* the canonical spelling of a role: the most common spelling in the data for that role (case and punctuation ignored),
* whether a role is SUPPORTED by an employee's skills: the skills that most other holders of that role have ("typical skills", held by at
  least TYPICAL_SHARE of them, only when at least MIN_BASELINE other people hold the role), and the share of those the employee has,
* which AI changes survive (rules in `verify`), and every number shown to the approver.
"""
import re
from collections import Counter

from .. import db

MIN_BASELINE = 3         # other holders needed before the data can say what a role "usually needs"
TYPICAL_SHARE = 0.4      # a skill is typical for a role when at least this share of its other holders have it
ADD_SUPPORT = 0.4        # to ADD a role the employee must have at least this share of its typical skills (when there is a baseline)
REMOVE_SUPPORT = 0.2     # to REMOVE a role the employee must have less than this share of its typical skills (baseline required)
MIN_EVIDENCE_SKILLS = 2  # an AI "add" must cite at least this many of the employee's OWN skills ...
MIN_EVIDENCE_LEVEL = 2   # ... each at least this level (Applied)
MAX_CHANGES = 5


def key(role: str) -> str:
    return re.sub(r"[^a-z0-9+#]+", " ", str(role or "").lower()).strip()


def clean(role: str) -> str:
    return re.sub(r"\s+", " ", str(role or "")).strip()[:60]


def role_counts() -> list[dict]:
    """Every role in use, merged by key: [{role (canonical spelling), holders, variants: [{spelling, count}]}], most held first."""
    spellings: dict[str, Counter] = {}
    for r in db.rows("SELECT role, COUNT(*) AS n FROM employee_roles GROUP BY role"):
        if key(r["role"]):
            spellings.setdefault(key(r["role"]), Counter())[r["role"]] += r["n"]
    out = []
    for k, c in spellings.items():
        best = sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))
        out.append({"role": best[0][0], "holders": sum(c.values()), "variants": [{"spelling": s, "count": n} for s, n in best]})
    return sorted(out, key=lambda x: (-x["holders"], x["role"].lower()))


def canonical_spelling(role: str, counts: list[dict] | None = None) -> str:
    """The spelling already used most for this role; a role nobody has yet keeps its (cleaned) spelling."""
    r = clean(role)
    for c in counts if counts is not None else role_counts():
        if key(c["role"]) == key(r):
            return c["role"]
    return r


def canonical_list(roles_in: list[str], counts: list[dict] | None = None) -> list[str]:
    """Canonical spelling for each role, one entry per role (case and punctuation ignored), order kept."""
    counts = counts if counts is not None else role_counts()
    out: dict[str, str] = {}
    for r in roles_in:
        if key(r):
            out.setdefault(key(r), canonical_spelling(r, counts))
    return list(out.values())


def typical_skills(role_key: str, exclude_employee: int | None = None) -> tuple[list[str], int]:
    """(skills most other holders of this role have, how many other holders there are)."""
    holders = [h["employee_id"] for h in db.rows("SELECT employee_id, role FROM employee_roles")
               if key(h["role"]) == role_key and h["employee_id"] != exclude_employee]
    if len(holders) < MIN_BASELINE:
        return [], len(holders)
    marks = ",".join("?" * len(holders))
    c = Counter(r["canonical_skill"] for r in db.rows(
        f"SELECT canonical_skill FROM employee_skills WHERE employee_id IN ({marks}) AND COALESCE(rating,0)>=1", tuple(holders)))
    return sorted(s for s, n in c.items() if n / len(holders) >= TYPICAL_SHARE), len(holders)


def support(emp_levels: dict[str, int], typical: list[str]) -> float | None:
    return None if not typical else sum(1 for s in typical if emp_levels.get(s, 0) >= 1) / len(typical)


def spelling_fixes(emp: dict, counts: list[dict] | None = None) -> list[dict]:
    """Code only, no AI: a role spelled differently from the canonical spelling (case, spacing, punctuation)."""
    counts = counts if counts is not None else role_counts()
    out = []
    for r in emp["roles"]:
        canon = canonical_spelling(r, counts)
        if canon != r:
            out.append({"action": "rename", "role": r, "to": canon, "skills": [], "source": "code",
                        "reason": f"Same role written differently; '{canon}' is the spelling used most."})
    return out


def _sentence(text) -> str:
    t = re.sub(r"\s+", " ", str(text or "")).strip()[:200]
    return "" if re.search(r"https?:|www\.|[<>`]", t) else t


def verify(emp: dict, proposed: list[dict], counts: list[dict] | None = None) -> tuple[list[dict], list[str]]:
    """Keep only the AI changes that pass the rules above. -> (kept changes, notes about what was dropped and why)."""
    counts = counts if counts is not None else role_counts()
    have = {key(r): r for r in emp["roles"]}
    levels = {s["canonical_skill"]: int(s["rating"] or 0) for s in emp["skills"]}
    lower = {k.lower(): k for k in levels}
    kept, notes, used = [], [], set()
    for ch in proposed or []:
        if not isinstance(ch, dict):
            continue
        action, role = ch.get("action"), clean(ch.get("role"))
        reason = _sentence(ch.get("reason"))
        if action == "rename":
            cur = have.get(key(role))
            to = canonical_spelling(ch.get("to") or "", counts)
            if not cur or not to or to == cur or ("rename", key(cur)) in used:
                notes.append(f"Dropped a rename of '{role}': it is not one of the employee's roles, or nothing would change.")
                continue
            used.add(("rename", key(cur)))
            kept.append({"action": "rename", "role": cur, "to": to, "skills": [], "source": "ai", "reason": reason})
        elif action == "add":
            to = canonical_spelling(role, counts)
            cited = [lower[str(x).strip().lower()] for x in ch.get("skills") or [] if str(x).strip().lower() in lower]
            cited = [s for s in dict.fromkeys(cited) if levels[s] >= MIN_EVIDENCE_LEVEL]
            if not to or key(to) in have or ("add", key(to)) in used:
                notes.append(f"Dropped adding '{role}': the employee already has it.")
                continue
            if len(cited) < MIN_EVIDENCE_SKILLS:
                notes.append(f"Dropped adding '{to}': fewer than {MIN_EVIDENCE_SKILLS} of the employee's own skills at level {MIN_EVIDENCE_LEVEL}+ back it.")
                continue
            typ, n = typical_skills(key(to), emp["id"])
            sup = support(levels, typ)
            if sup is not None and sup < ADD_SUPPORT:
                notes.append(f"Dropped adding '{to}': the employee has {sup:.0%} of the skills its {n} other holders usually have (needs {ADD_SUPPORT:.0%}).")
                continue
            used.add(("add", key(to)))
            fact = f"Backed by {', '.join(f'{s} {levels[s]}/4' for s in cited[:4])}."
            if sup is not None:
                fact += f" Has {sup:.0%} of the skills its {n} other holders usually have."
            kept.append({"action": "add", "role": to, "to": "", "skills": cited[:6], "source": "ai", "reason": (reason + " " + fact).strip()})
        elif action == "remove":
            cur = have.get(key(role))
            if not cur:
                continue
            typ, n = typical_skills(key(cur), emp["id"])
            sup = support(levels, typ)
            if sup is None:
                notes.append(f"Kept '{cur}': fewer than {MIN_BASELINE} other people hold it, so the data cannot say what it needs.")
                continue
            if sup >= REMOVE_SUPPORT:
                notes.append(f"Kept '{cur}': the employee has {sup:.0%} of the skills its {n} other holders usually have.")
                continue
            if len(have) <= 1:
                notes.append(f"Kept '{cur}': it is the employee's only role.")
                continue
            kept.append({"action": "remove", "role": cur, "to": "", "skills": [], "source": "ai",
                         "reason": (reason + f" The employee has {sup:.0%} of the skills its {n} other holders usually have.").strip()})
    return kept[:MAX_CHANGES], notes


def apply_to_list(current: list[str], changes: list[dict]) -> list[str]:
    """The roles after the changes (rename, add, remove). Roles are unique ignoring case; spellings come from the changes."""
    roles = list(current)
    for ch in changes:
        k = key(ch["role"])
        if ch["action"] == "rename":
            roles = [ch["to"] if key(r) == k else r for r in roles]
        elif ch["action"] == "add":
            roles.append(ch["role"])
        elif ch["action"] == "remove":
            roles = [r for r in roles if key(r) != k]
    out, seen = [], set()
    for r in roles:
        if key(r) and key(r) not in seen:
            seen.add(key(r))
            out.append(clean(r))
    return out
