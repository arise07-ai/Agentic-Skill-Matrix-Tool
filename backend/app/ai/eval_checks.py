"""RULE-BASED CHECKS for staffing and learning-plan answers. No LLM, no API key, exact results.

Staffing recommendations are checked against the database, not an LLM judge. Learning-plan checks validate skill gaps and
hours; RAGAS separately evaluates the answer wording and context relevance.

    python -m app.ai.eval_checks [dataset.json]        # default: ../samples/eval_dataset.json

A row is checked against the CURRENT database only if the employee data has not changed since the answer was logged
(compared by the fingerprint stored with the row); otherwise it is reported as skipped, never as passed.
"""
import json
import re
import sys
from pathlib import Path

from .. import db
from ..services import groups
from ..services.seniority import LEVEL_NAMES
from . import eval_log

DEFAULT_DATASET = Path(__file__).resolve().parents[3] / "samples" / "eval_dataset.json"
URL = re.compile(r"https?://[^\s)\]>\"']+")


def _ratings() -> dict[int, dict[str, int]]:
    out: dict[int, dict[str, int]] = {}
    for r in db.rows("SELECT employee_id, canonical_skill, rating FROM employee_skills"):
        out.setdefault(r["employee_id"], groups.Ratings())[r["canonical_skill"]] = r["rating"] or 0  # broad skills count their tools
    return out


def _check(name: str, ok: bool, detail: str = "") -> dict:
    return {"check": name, "passed": bool(ok), "detail": "" if ok else detail}


def check_staffing(row: dict, ratings: dict, employees: dict[int, str]) -> list[dict]:
    m = row["meta"]
    requirements = m["requirements"]
    reqs = {r["skill"]: r["level"] for r in requirements}
    min_people = {r["skill"]: r.get("min_people", 1) for r in requirements}
    team = m["team_details"]
    ids = [d["id"] for d in team]
    have = lambda eid, skill: ratings.get(eid, {}).get(skill, 0)  # noqa: E731
    out = [
        _check("team_members_exist", all(i in employees for i in ids), f"unknown employee id(s): {[i for i in ids if i not in employees]}"),
        _check("no_duplicate_members", len(set(ids)) == len(ids), "the same person appears twice"),
    ]
    cap = min(m["team_size"], len(employees))
    if m["uncovered"]:  # nobody is added as filler while a required skill is still missing
        out.append(_check("team_size_within_head_count", 0 < len(ids) <= cap or not ids, f"{len(ids)} people suggested, at most {cap} allowed"))
    else:
        out.append(_check("team_size_as_requested", len(ids) == cap, f"{len(ids)} people suggested, {cap} expected"))
    contributors = [d for d in team if d.get("role", "contributor") == "contributor"]
    extras = [d for d in team if d.get("role") == "extra"]
    out.append(_check("contributors_add_something", all(d["adds"] for d in contributors),
                      "a team member is not an extra but adds no required skill: " + ", ".join(d["name"] for d in contributors if not d["adds"])))
    out.append(_check("no_extras_while_skills_missing", not (extras and m["uncovered"]),
                      "extra people were added although required skills are still uncovered"))
    out.append(_check("extras_add_nothing", all(not d["adds"] for d in extras), "an extra is listed with added skills"))
    L = m.get("learning", [])
    team_ids = set(ids)
    bad = [f"{x['name']}: {s['skill']}" for x in L for s in x["skills"] if have(x["employee_id"], s["skill"]) >= s["target"]]
    out.append(_check("learners_really_lack_the_skill", not bad, "already at the target level: " + "; ".join(bad[:5])))
    off = [s["skill"] for x in L for s in x["skills"] if s["skill"] not in m["nobody"]]
    out.append(_check("learning_only_for_skills_nobody_has", not off, "learning suggested for skills someone already has: " + ", ".join(off)))
    out.append(_check("learning_given_for_every_missing_skill", all(any(s["skill"] == k for x in L for s in x["skills"]) for k in m["nobody"]) or not employees,
                      "a skill nobody has got no learning suggestion"))

    bad_meet, bad_gap, bad_adds = [], [], []
    team_coverage = {skill: 0 for skill in reqs}
    adders = {skill: 0 for skill in reqs}
    for member in team:
        expected_meets = {skill for skill, level in reqs.items() if have(member["id"], skill) >= level}
        expected_gaps = set(reqs) - expected_meets
        if set(member["meets"]) != expected_meets:
            bad_meet.append(member["name"])
        if set(member["below_target"]) != expected_gaps:
            bad_gap.append(member["name"])
        # The team is built skill by skill (highest rating first), so a person may meet a skill that someone else was chosen for.
        # A person may only be credited with a skill he meets.
        if not set(member["adds"]) <= expected_meets:
            bad_adds.append(member["name"])
        for skill in member["adds"]:
            if skill in adders:
                adders[skill] += 1
        for skill in expected_meets:
            team_coverage[skill] += 1
    over = [skill for skill, n in adders.items() if n > min_people[skill]]
    unassigned = [skill for skill in reqs if any(have(d["id"], skill) >= reqs[skill] for d in contributors) and not adders[skill]]
    bad_adds += [f"{skill} (credited to {adders[skill]}, needs {min_people[skill]})" for skill in over]
    bad_adds += [f"{skill} (met by the team but credited to nobody)" for skill in unassigned]
    out.append(_check("candidate_skill_evidence_matches_database", not bad_meet and not bad_gap,
                      "claimed meets/below-target skills do not match database ratings for: " + ", ".join(bad_meet + bad_gap)))
    out.append(_check("team_contributions_match_coverage", not bad_adds,
                      "claimed team contributions do not match remaining required coverage for: " + ", ".join(bad_adds)))
    expected_uncovered = {skill for skill, count in team_coverage.items() if count < min_people[skill]}
    actual_uncovered = set(m["uncovered"])
    out.append(_check("uncovered_skills_match_database",
                      actual_uncovered == expected_uncovered,
                      f"expected {sorted(expected_uncovered)}, got {sorted(actual_uncovered)}"))
    company_coverage = {
        skill: sum(have(employee_id, skill) >= reqs[skill] for employee_id in employees)
        for skill in reqs
    }
    expected_nobody = {skill for skill, count in company_coverage.items() if count < min_people[skill]}
    actual_nobody = set(m["nobody"])
    out.append(_check("company_skill_coverage_matches_database",
                      actual_nobody == expected_nobody,
                      f"expected nobody for {sorted(expected_nobody)}, got {sorted(actual_nobody)}"))
    expected_team_limited = expected_uncovered - expected_nobody
    if set(m.get("team_limited", [])) != expected_team_limited:
        out.append(_check("team_size_limited_skills_match_database", False,
                          f"expected {sorted(expected_team_limited)}, got {sorted(m.get('team_limited', []))}"))
    else:
        out.append(_check("team_size_limited_skills_match_database", True))
    NC = m.get("not_chosen", [])  # runners-up the answer explains (rows logged before this field existed have none)
    out.append(_check("not_chosen_not_in_team", all(x["id"] not in team_ids for x in NC), "someone listed as not chosen is in the team"))
    bad_nc = [f"{x['name']}: {s}" for x in NC for s in x["meets"] if have(x["id"], s) < reqs.get(s, 99)] + \
             [f"{x['name']}: {s}" for x in NC for s in x["below_target"] if have(x["id"], s) >= reqs.get(s, 0)]
    out.append(_check("not_chosen_claims_true", not bad_nc, "claimed wrongly for a person who was not chosen: " + "; ".join(bad_nc[:5])))
    names = {n.lower() for i, n in employees.items() if i in set(ids)}
    learners = {x["employee_id"] for x in L} | {x["id"] for x in NC}
    others = [n for i, n in employees.items() if i not in set(ids) | learners and len(n) >= 6 and re.search(r"\b" + re.escape(n) + r"\b", row["answer"], re.I)]
    out.append(_check("answer_mentions_only_the_team", all(n in row["answer"].lower() for n in names) and not others,
                      f"names missing from the answer or people outside the team mentioned: {others[:3]}"))
    return out


def check_learning(row: dict, ratings: dict, employees: dict[int, str]) -> list[dict]:
    m = row["meta"]
    eid, gaps, plan = m["employee_id"], m["gaps"], m["plan"]
    have = lambda skill: ratings.get(eid, {}).get(skill, 0)  # noqa: E731
    total = m["weeks"] * m["hours_per_week"]
    out = [_check("employee_exists", eid in employees, f"unknown employee id {eid}")]
    wrong = [g["skill"] for g in gaps if have(g["skill"]) != g["current"] or g["current"] >= g["target"]]
    out.append(_check("gaps_match_the_profile", not wrong, "gap does not match the employee's rating: " + ", ".join(wrong)))
    listed = {g["skill"] for g in gaps}
    missed = [r["skill"] for r in m["requested"] if have(r["skill"]) < r["target"] and r["skill"] not in listed]
    extra = [r["skill"] for r in m["requested"] if have(r["skill"]) >= r["target"] and r["skill"] in listed]
    out.append(_check("no_missing_or_needless_gaps", not missed and not extra, f"missing: {missed}; needless: {extra}"))
    hours = sum(b["hours"] for b in plan)
    fits = len(plan) == len(gaps)
    slack = 0.5 * len(plan)  # hours are rounded to half an hour per skill
    out.append(_check("hours_add_up", (abs(hours - total) <= slack) if fits else (hours <= total + slack),
                      f"planned {hours:g} h, available {total} h"))
    out.append(_check("weeks_inside_the_plan", all(1 <= b["start_week"] <= b["end_week"] <= m["weeks"] for b in plan), "a block is outside the weeks"))
    by_delta: dict[int, list[float]] = {}
    for g in gaps:
        for b in plan:
            if b["skill"] == g["skill"]:
                by_delta.setdefault(g["delta"], []).append(b["hours"])
    out.append(_check("equal_gaps_get_equal_time", all(max(v) - min(v) <= 0.5 for v in by_delta.values()), f"hours by gap size: {by_delta}"))
    allowed = {u.rstrip(".,;") for c in row["contexts"] for u in URL.findall(c)}
    foreign = [u for u in URL.findall(row["answer"]) if u.rstrip(".,;") not in allowed]
    out.append(_check("links_only_from_retrieved_courses", not foreign, "link(s) not among the retrieved courses: " + ", ".join(foreign[:3])))
    out.append(_check("every_planned_skill_is_in_the_text", all(b["skill"] in row["answer"] for b in plan), "a planned skill is missing from the plan text"))
    split = [x for x in m.get("hours_breakdown", []) if x.get("known")]
    if "hours_breakdown" in m:  # rows logged before the hours breakdown existed have none
        bad = [f"{x['skill']}: {x['course_hours']:g} h courses + {x['practice_hours']:g} h practice vs {x['planned']:g} h planned"
               for x in split if (x["practice_hours"] != 0 if x.get("over") else abs(x["course_hours"] + x["practice_hours"] - x["planned"]) > 0.15)]
        out.append(_check("practice_fills_the_leftover_hours", not bad, "course and practice hours do not fill the planned hours: " + "; ".join(bad[:3])))
        out.append(_check("breakdown_matches_the_schedule", all(any(b["skill"] == x["skill"] and abs(b["hours"] - x["planned"]) < 0.01 for b in plan) for x in split),
                          "an hours breakdown line does not match the planned hours of that skill"))
    return out


# ------------------------------------------------------------------------------------------------ Learning coach
_HOURS_IN_TEXT = re.compile(r"(\d+(?:\.\d+)?)\s*(?:h|hrs?|hours?)\b", re.I)
_WEEKS_IN_TEXT = re.compile(r"(\d+)(?:\s*[-–]\s*(\d+))?\s*weeks?\b", re.I)  # "6 weeks", "2-3 weeks"
_WEEK_NUMBER = re.compile(r"\bweeks?\s*(\d+)(?:\s*[-–]\s*(\d+))?", re.I)  # "week 4", "weeks 1-6"


def check_learning_coach(row: dict, ratings: dict, employees: dict[int, str]) -> list[dict]:
    """The coach may only quote hours and weeks that the verified plan contains, and no link outside the plan."""
    m, answer = row["meta"], row["answer"]
    allowed = {round(float(x), 2) for x in m.get("allowed_hours", [])}
    stated = {round(float(n), 2) for n in _HOURS_IN_TEXT.findall(answer)}
    foreign = sorted(stated - allowed)
    out = [_check("employee_exists", m.get("employee_id") in employees, f"unknown employee id {m.get('employee_id')}"),
           _check("hours_stated_are_in_the_plan", not foreign, "hours not found in the plan: " + ", ".join(f"{h:g}" for h in foreign[:5]))]
    weeks = m.get("weeks") or 0
    bad_weeks = sorted({int(x) for rx in (_WEEKS_IN_TEXT, _WEEK_NUMBER) for a, b in rx.findall(answer) for x in (a, b) if x and int(x) > weeks})
    out.append(_check("weeks_stated_are_inside_the_plan", not bad_weeks, f"weeks beyond the {weeks}-week plan: {bad_weeks}"))
    allowed_urls = {u.rstrip(".,;") for c in row["contexts"] for u in URL.findall(c)}
    links = [u for u in URL.findall(answer) if u.rstrip(".,;") not in allowed_urls]
    out.append(_check("links_only_from_the_plan", not links, "link(s) not in the plan: " + ", ".join(links[:3])))
    return out


# ------------------------------------------------------------------------------------------------ Ask the matrix (chat)
_NEGATION = re.compile(r"\b(no one|nobody|none|no employee|not found|no-one|isn't|is not|are not|aren't|doesn't|does not|do not|don't|no data|"
                       r"no information|not available|cannot|can't|couldn't|unable|not in|no record|not stored|not recorded|not included)\b", re.I)
_INVENTED = re.compile(r"[$₱€£]\s?\d|\b\d[\d,.]*\s?(?:k|php|usd|eur|pesos|dollars)\b|\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.? \d{1,2}\b|"
                       r"\b\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?\b", re.I)


# --- level names. The scale is 0 None, 1 Learned, 2 Applied, 3 Experienced, 4 Mastered. A number and its name must agree.
_NAMES = "None|Learned|Applied|Experienced|Mastered"
_PAIR_NUM_FIRST = re.compile(r"(?<![\d.])([0-4])(?:\s*/\s*4)?\s*[(\-–—:,]\s*\(?\s*(" + _NAMES + r")\b", re.I)   # 4 (Mastered), 4/4 - Mastered
_PAIR_NAME_FIRST = re.compile(r"\b(" + _NAMES + r")\s*[(\-–—:]\s*(?:level\s*)?([0-4])\b(?!\s*(?:people|persons|employees|members))", re.I)   # Mastered (4), Mastered (level 4); not "Applied: 3 people"
_OLD_SCALE = re.compile(r"\b[0-5]\s*/\s*5\b|\bout of 5\b|\blevel\s*5\b", re.I)                                          # the retired 1-5 scale
_WORD_LEVEL = re.compile(r"\b(Learned|Applied|Experienced|Mastered)\b")  # a level name on its own ("None" is an everyday word, so it is only read next to a number)
_NAME_TO_LEVEL = {v: k for k, v in LEVEL_NAMES.items()}


def wrong_level_pairs(answer: str) -> list[str]:
    """Every 'number + name' (or 'name + number') in the answer whose name does not belong to that number."""
    bad = []
    for m in _PAIR_NUM_FIRST.finditer(answer):
        if LEVEL_NAMES[int(m.group(1))].lower() != m.group(2).lower():
            bad.append(f"{m.group(0).strip()} (level {m.group(1)} is {LEVEL_NAMES[int(m.group(1))]})")
    for m in _PAIR_NAME_FIRST.finditer(answer):
        if LEVEL_NAMES[int(m.group(2))].lower() != m.group(1).lower():
            bad.append(f"{m.group(0).strip()} (level {m.group(2)} is {LEVEL_NAMES[int(m.group(2))]})")
    return bad


def _level_stated_near(answer: str, anchor: str, others: list[str] = ()) -> int | None:
    """The level the answer gives right after `anchor` (a skill or a person's name) on the same line: '... Python (Mastered)', '... Ann Lee: level 4'.
    None when it states none. The search stops at a line end, a comma, a semicolon, a digit (employee numbers, counts) for names, or the next person."""
    for m in re.finditer(r"(?<!\w)" + re.escape(anchor) + r"(?!\w)", answer, re.I):
        seg = answer[m.end():m.end() + 45]
        cut = re.search(r"[\n;]|,(?!\s*(?:level|" + _NAMES + r")\b)", seg)
        seg = seg[:cut.start()] if cut else seg
        for o in others:
            i = seg.lower().find(o.lower())
            if o.lower() != anchor.lower() and i != -1:
                seg = seg[:i]
        n = re.search(r"(?<![A-Za-z\d.])(?:level\s*)?([0-4])\b(?!\s*(?:weeks?|hours?|h\b|%|people|persons|employees|members))", seg, re.I)
        w = _WORD_LEVEL.search(seg) or re.search(r"\(\s*(" + _NAMES + r")\s*\)", seg, re.I)
        if n:
            return int(n.group(1))
        if w:
            return _NAME_TO_LEVEL.get(w.group(1).capitalize())
    return None


def _mentioned(answer: str, employees: dict[int, str]) -> set[str]:
    return {n for n in employees.values() if len(n) >= 4 and re.search(r"(?<!\w)" + re.escape(n) + r"(?!\w)", answer, re.I)}


def _levels_per_person(answer: str, skill: str | None, who: set[str], ratings: dict, employees: dict[int, str]) -> dict:
    """When the answer gives a level next to a person's name, it must be that person's real level in the asked skill.
    Needs the skill in the case (older logged rows have none: then there is nothing to compare and the check passes)."""
    if not skill:
        return _check("levels_given_per_person_are_true", True)
    ids = {n: i for i, n in employees.items()}
    wrong = []
    for n in sorted(who):
        said = _level_stated_near(answer, n, [o for o in who if o != n])
        real = (ratings.get(ids.get(n)) or {}).get(skill)
        if said is not None and real is not None and said != real:
            wrong.append(f"{n}: said {said} ({LEVEL_NAMES[said]}), is {real} ({LEVEL_NAMES.get(real, real)})")
    return _check("levels_given_per_person_are_true", not wrong, "; ".join(wrong[:4]))


def check_chat(row: dict, ratings: dict, employees: dict[int, str]) -> list[dict]:
    m, answer = row["meta"], row["answer"]
    exp = m.get("expect") or {}
    kind = exp.get("type")
    who = _mentioned(answer, employees)
    evidence = " ".join(c for c in row["contexts"] if c.startswith("Tool ")).lower()  # this turn's tool results; earlier turns are not evidence
    out = [_check("named_people_were_retrieved", all(n.lower() in evidence for n in who),
                  "named someone the tools never returned: " + ", ".join(n for n in who if n.lower() not in evidence)),
           _check("level_names_match_their_numbers", not wrong_level_pairs(answer), "; ".join(wrong_level_pairs(answer)[:4])),
           _check("uses_the_0_to_4_scale", not _OLD_SCALE.search(answer),
                  "uses the retired 1-5 scale: " + (_OLD_SCALE.search(answer).group(0) if _OLD_SCALE.search(answer) else ""))]
    if not kind:  # a real question from a person: only the general checks apply
        return out
    out.append(_check("looked_the_data_up", bool(m.get("tool_calls")) or kind == "not_in_data", "answered without calling any tool"))
    if kind == "count":
        v = exp["value"]
        out.append(_check("states_the_right_count", bool(re.search(rf"(?<![\d.,]){v}(?![\d])", answer)), f"the answer does not state {v}"))
    elif kind == "people":
        want = set(exp["names"])
        out.append(_check("lists_exactly_the_right_people", who == want,
                          f"missing: {sorted(want - who)}; not entitled: {sorted(who - want)}"))
        out.append(_levels_per_person(answer, exp.get("skill"), who, ratings, employees))
    elif kind == "skills":
        low = answer.lower()
        miss = [k for k in exp["skills"] if k.lower() not in low]
        out.append(_check("lists_all_the_skills", not miss, "skills missing from the answer: " + ", ".join(miss[:6])))
        wrong = []
        for k, v in exp["skills"].items():
            for mm in re.finditer(re.escape(k) + r"[^\n\d]{0,14}?([0-4])\b", answer, re.I):
                if int(mm.group(1)) != v:
                    wrong.append(f"{k}: said {mm.group(1)}, is {v}")
        for k, v in exp["skills"].items():  # "Python (Mastered)" with no number
            for mm in re.finditer(re.escape(k) + r"[^\n,;\d]{0,24}?\b(Learned|Applied|Experienced|Mastered)\b", answer):
                if _NAME_TO_LEVEL[mm.group(1)] != v:
                    wrong.append(f"{k}: said {mm.group(1)}, is {LEVEL_NAMES[v]}")
        out.append(_check("ratings_quoted_are_true", not wrong, "; ".join(wrong[:5])))
    elif kind == "role":
        miss = [r for r in exp["roles"] if r.lower() not in answer.lower()]
        out.append(_check("states_the_role", not miss, "role missing: " + ", ".join(miss)))
    elif kind == "top":
        want = set(exp["names"])
        out.append(_check("names_the_top_rated", want <= who, f"missing: {sorted(want - who)}"))
        out.append(_check("states_the_top_rating", bool(re.search(rf"\b{exp['rating']}\b", answer)), f"does not state rating {exp['rating']}"))
        out.append(_levels_per_person(answer, exp.get("skill"), who, ratings, employees))
    elif kind == "roles":
        bad = [r for r, n in exp["roles"].items() if not re.search(re.escape(r) + r"[^\n]{0,50}?\b" + str(n) + r"\b", answer, re.I)]
        out.append(_check("roles_and_head_counts_right", not bad, "role/count not found: " + ", ".join(bad[:5])))
    elif kind == "nobody":
        out.append(_check("says_nobody_has_it", bool(_NEGATION.search(answer)) and not who,
                          "should say nobody has it" + (f", but names {sorted(who)}" if who else "")))
    elif kind == "not_in_data":
        out.append(_check("says_it_is_not_available", bool(_NEGATION.search(answer)), "should say the data does not contain it"))
        out.append(_check("invents_no_value", not _INVENTED.search(answer), "gives a value the data cannot contain"))
    return out


def run_checks(rows: list[dict]) -> dict:
    """-> {"rows": [...], "summary": {check: {passed, total}}, "skipped": n}. Needs the app database."""
    db.init_db()
    ratings = _ratings()
    employees = {r["id"]: r["name"] for r in db.rows("SELECT id, name FROM employees")}
    fingerprint = eval_log.data_fingerprint()
    out_rows, summary, skipped = [], {}, 0
    for r in rows:
        meta = r.get("meta") or {}
        kind = meta.get("kind") or r.get("kind")  # a logged row keeps its kind at the top level, not inside meta
        fn = {"staffing": check_staffing, "learning_path": check_learning, "chat": check_chat, "learning_coach": check_learning_coach}.get(kind)
        base = {"question": r["question"][:120], "kind": kind}
        if not fn:
            continue
        needed = {"staffing": "team_details", "chat": "tool_calls", "learning_coach": "allowed_hours"}.get(kind, "gaps")
        if needed not in meta:
            out_rows.append({**base, "skipped": "logged by an older version of the app"})
            skipped += 1
            continue
        if meta.get("data_fingerprint") != fingerprint:
            out_rows.append({**base, "skipped": "employee data changed since this answer was logged"})
            skipped += 1
            continue
        try:
            results = fn(r, ratings, employees)
        except Exception as e:  # noqa: BLE001  a malformed row must not stop the others
            out_rows.append({**base, "skipped": f"could not be checked ({type(e).__name__}: {e})"})
            skipped += 1
            continue
        out_rows.append({**base, "results": results})
        for c in results:
            s = summary.setdefault(f"{kind}/{c['check']}", {"passed": 0, "total": 0})
            s["total"] += 1
            s["passed"] += c["passed"]
    return {"rows": out_rows, "summary": summary, "skipped": skipped}


def print_report(res: dict) -> None:
    print("\nRule-based checks (exact, no LLM)\n")
    for r in res["rows"]:
        if r.get("skipped"):
            print(f"  SKIPPED [{r['kind']}] {r['question']}  ({r['skipped']})")
            continue
        failed = [c for c in r["results"] if not c["passed"]]
        print(f"  {'PASS' if not failed else 'FAIL'}    [{r['kind']}] {r['question']}")
        for c in failed:
            print(f"          - {c['check']}: {c['detail']}")
    print()
    for k, v in sorted(res["summary"].items()):
        print(f"  {k:55} {v['passed']}/{v['total']}")
    if res["skipped"]:
        print(f"\n  {res['skipped']} row(s) skipped. Skipped is not passed.")
    if not res["summary"]:
        print("  Nothing could be checked. Ask questions in the app, then rebuild the dataset (make_eval_dataset).")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    path = Path(args[0]) if args else DEFAULT_DATASET
    if not path.exists():
        raise SystemExit(f"Dataset not found: {path}\nCreate it with:  python -m app.ai.make_eval_dataset")
    print_report(run_checks(json.loads(path.read_text(encoding="utf-8"))))
