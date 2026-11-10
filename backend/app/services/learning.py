"""Learning paths: employee gaps -> courses and videos (your catalog, Microsoft Learn, YouTube) -> Markdown study plan.

THE MODEL PLANS, THE CODE VERIFIES. The model (ai/orchestration.py) chooses, for every skill, the study course and the video from the
retrieved candidates and writes a one-line reason and a small practice task for this person and project. Code then checks every choice
(verify_picks) and builds the whole plan: gaps, schedule, hours, course list, hours breakdown, milestones. A choice that fails a check is
replaced by the rule-based one, so nothing the model says can change a number or add a course. Without an LLM (or when it fails) the same
plan is built from the rules alone. `mode` = llm (model choices accepted) | template (rules only) | none (no gaps).
A manager, HR or admin can edit the finished plan; check_edited_plan() re-checks the facts of an edited plan.
"""
import json
import logging
import math
import re

from ..ai import eval_log, llm, orchestration, prompts
from ..ai.conversation import cached_conversational_reply, normalize_messages
from ..ai.guardrails import GuardrailViolation, check_input, check_output
from ..schemas import LearningCoachRequest, LearningPlanCheckRequest, LearningRequest
from . import groups, repo
from .seniority import LEVEL_NAMES
from .courses import search_courses

log = logging.getLogger(__name__)


def _level(target: int) -> str:
    return "beginner" if target <= 1 else "intermediate" if target == 2 else "expert"  # 1 Learned, 2 Applied, 3 Experienced / 4 Mastered


def short_title(text: str, n: int = 70) -> str:
    """A project name from a long project description: its first sentence, cut to n characters."""
    t = " ".join((text or "").split())
    if not t:
        return ""
    first = re.split(r"(?<=[.!?])\s", t, maxsplit=1)[0]
    return first if len(first) <= n else first[: n - 1].rstrip() + "…"


def allocate_hours(gaps: list[dict], weeks: int, hours_per_week: int, min_hours: float = 2.0) -> list[dict]:
    """Split ALL the hours (weeks x hours per week) over the gaps in proportion to their size, so equal gaps get equal time.
    A skill is given a block of hours, not whole weeks: a week may hold the end of one skill and the start of the next.
    Gaps that would get under `min_hours` are left out (largest gaps are kept). -> [{gap, hours, start_week, end_week}]"""
    total = weeks * hours_per_week
    keep = sorted(gaps, key=lambda g: -g["delta"])[: max(1, int(total // min_hours))]
    wsum = sum(g["delta"] for g in keep) or 1
    out, used = [], 0.0
    for g in keep:
        h = total * g["delta"] / wsum
        start, end = used, used + h
        out.append({"gap": g, "hours": round(h * 2) / 2,
                    "start_week": min(weeks, int(start // hours_per_week) + 1),
                    "end_week": min(weeks, max(int(start // hours_per_week) + 1, math.ceil(end / hours_per_week - 1e-9)))})
        used = end
    return out


def _weeks_label(a: int, b: int) -> str:
    return f"Week {a}" if a == b else f"Weeks {a}–{b}"


def _related_skills(emp: dict, gaps: list[dict]) -> None:
    """Note, per gap, a skill the employee already has (rating 2+, Applied or better) that is close in meaning. It does not change the level."""
    try:
        from ..config import get_settings
        from . import projects

        have = {s["canonical_skill"]: (s["rating"] or 0) for s in emp["skills"]}
        strong = [k for k, v in have.items() if v >= 2]
        if not strong:
            return
        vecs = projects._skill_vectors([g["skill"] for g in gaps] + strong)
        floor = get_settings().adjacency_min_similarity
        for g in gaps:
            if g["skill"] not in vecs:
                continue
            best, sim = None, 0.0
            for k in strong:
                if k != g["skill"] and k in vecs:
                    v = float(vecs[g["skill"]] @ vecs[k])
                    if v > sim:
                        best, sim = k, v
            if best and sim >= floor:
                g["related_skill"], g["related_rating"] = best, have[best]
    except Exception:  # noqa: BLE001  a missing vector index must not break a plan
        log.debug("Related-skill lookup unavailable", exc_info=True)


def time_plan_lines(plan: list[dict]) -> list[str]:
    return [f"{_weeks_label(b['start_week'], b['end_week'])}: {b['gap']['skill']} ({b['gap']['current']} → {b['gap']['target']}), about {b['hours']:g} h" for b in plan]


def _level_rank(text: str | None) -> int | None:
    t = (text or "").lower()
    return 1 if "begin" in t or "intro" in t else 2 if "intermed" in t else 3 if "adv" in t or "expert" in t else None


def pick_resources(cs: list[dict], hours: float, target: int | None = None) -> tuple[dict | None, dict | None]:
    """The course to study and the video to watch for one skill. Study course: among the non-video courses, one that fits the hours
    planned for the skill (an unknown length counts as fitting) comes first, then the one whose level is closest to the target level,
    then real data before sample data, then the retrieval order. If none fits, the best of the rest is taken and the plan says so."""
    want = _level_rank(_level(target)) if target else None

    def key(item: tuple[int, dict]) -> tuple:
        i, c = item
        d = c.get("duration_hours")
        rank = _level_rank(c.get("level"))
        return (bool(d and d > hours), abs(rank - want) if want and rank else 0, bool(c.get("is_sample")), i)

    courses_only = [(i, c) for i, c in enumerate(cs) if c.get("kind") != "video"]
    study = min(courses_only, key=key)[1] if courses_only else (cs[0] if cs else None)
    videos = [(i, c) for i, c in enumerate(cs) if c.get("kind") == "video" and c is not study]
    watch = min(videos, key=lambda t: (bool(t[1].get("is_sample")), t[0]))[1] if videos else None
    return study, watch


def _resources(skill: str, hours: float, target: int | None, courses: dict[str, list[dict]], picks: dict | None) -> tuple[dict | None, dict | None]:
    """(study, watch) for one skill: the model's pick when the code accepted it (verify_picks), otherwise the rule-based pick."""
    study, watch = pick_resources(courses.get(skill, []), hours, target)
    chosen = (picks or {}).get(skill) or {}
    return chosen.get("study") or study, chosen.get("watch") or watch


def hours_breakdown(plan: list[dict], courses: dict[str, list[dict]], picks: dict | None = None) -> list[dict]:
    """Per skill: planned hours = course hours + practice and review hours. The study course counts in full; the video counts only if the
    total still fits. Hours the courses do not fill become practice and review (never negative). `known` is False when no course
    length is listed, and then no split is claimed. `over` is True when the study course is longer than the planned hours."""
    out = []
    for b in plan:
        skill, planned = b["gap"]["skill"], b["hours"]
        study, watch = _resources(skill, planned, b["gap"]["target"], courses, picks)
        counted, total = [], 0.0
        for c in (study, watch):
            d = c.get("duration_hours") if c else None
            if d and (not counted or total + d <= planned):
                counted.append(f"{c['title']} ({d:g} h)")
                total += d
        out.append({"skill": skill, "planned": planned, "course_hours": round(total, 1), "practice_hours": round(max(planned - total, 0), 1),
                    "counted": counted, "known": bool(counted), "over": total > planned})
    return out


def hours_section(breakdown: list[dict]) -> str:
    """The code-built 'Hours Breakdown' section: what the course hours are and what the remaining hours are for."""
    lines = ["Course time is counted against each skill's hours. The rest is hands-on practice and review.", ""]
    tp = tc = tr = 0.0
    all_known = True
    for x in breakdown:
        if not x["known"]:
            all_known = False
            lines.append(f"- {x['skill']}: {x['planned']:g} h planned; course lengths are not listed, so the split between courses and practice is not computed.")
            continue
        lines.append(f"- {x['skill']}: {x['planned']:g} h planned; {x['course_hours']:g} h courses; {x['practice_hours']:g} h practice and review")
        lines.append(f"  - Courses counted: {'; '.join(x['counted'])}.")
        if x["over"]:
            lines.append(f"  - Time check: this course takes about {x['course_hours']:g} h but only about {x['planned']:g} h are planned. "
                         f"Do the first {x['planned']:g} h (the core modules) or add time.")
        elif x["practice_hours"] > 0:
            lines.append(f"  - Practice and review ({x['practice_hours']:g} h): apply {x['skill']} to a small real task from the project backlog, "
                         "then demonstrate it to your lead.")
        tp, tc, tr = tp + x["planned"], tc + x["course_hours"], tr + x["practice_hours"]
    if all_known and breakdown:
        lines.append(f"- Total: {tp:g} h planned; {tc:g} h courses; {tr:g} h practice and review")
    return "\n".join(lines)


_HOURS_LINE = re.compile(r"(?m)^- (.+?): ([\d.]+) h planned; ([\d.]+) h courses; ([\d.]+) h practice and review\s*$")


def parse_hours_breakdown(markdown: str) -> list[dict]:
    """Read the lines hours_section wrote back out of a plan (the Total line has skill 'Total'). Empty when the plan has none."""
    return [{"skill": m.group(1), "planned": float(m.group(2)), "course_hours": float(m.group(3)), "practice_hours": float(m.group(4))}
            for m in _HOURS_LINE.finditer(_section(markdown, "Hours Breakdown"))]


def _put_section_before(markdown: str, heading: str, content: str, before: str) -> str:
    """Replace ## heading with content, or insert it just before ## `before` (at the end if that is missing)."""
    section = f"## {heading}\n\n{content.strip()}\n"
    if re.search(rf"(?m)^## {re.escape(heading)}\s*$", markdown):
        return _replace_section(markdown, heading, content)
    m = re.search(rf"(?m)^## {re.escape(before)}\s*$", markdown)
    if m:
        return f"{markdown[:m.start()]}{section}\n{markdown[m.start():]}"
    return f"{markdown.rstrip()}\n\n{section}"


def _section(markdown: str, heading: str) -> str:
    match = re.search(rf"(?m)^## {re.escape(heading)}\s*$", markdown)
    if not match:
        return ""
    following = markdown[match.end():]
    end = re.search(r"(?m)^## ", following)
    return following[:end.start()] if end else following


def _replace_section(markdown: str, heading: str, content: str) -> str:
    pattern = re.compile(rf"(?m)^## {re.escape(heading)}\s*$")
    match = pattern.search(markdown)
    section = f"## {heading}\n\n{content.strip()}\n"
    if not match:
        return f"{markdown.rstrip()}\n\n{section}"
    following = markdown[match.end():]
    end = re.search(r"(?m)^## ", following)
    end_at = match.end() + end.start() if end else len(markdown)
    return f"{markdown[:match.start()]}{section}\n{markdown[end_at:]}".rstrip() + "\n"


_UNSAFE_TEXT = re.compile(r"https?://|www\.|\d+(?:\.\d+)?\s*(?:h\b|hrs?\b|hours?\b|weeks?\b|days?\b)|[`#|<>\[\]]|\bweek\s*\d", re.I)


def _clean_text(value: object, limit: int) -> str:
    """One short plain sentence from the model, or "" when it must not be shown: no links, no markdown, and no hours, weeks or days
    (the schedule is computed, so the model may not state one)."""
    text = " ".join(str(value or "").split()).strip(" \"'")
    if not text or len(text) > limit or _UNSAFE_TEXT.search(text):
        return ""
    return text if text[-1] in ".!?" else text + "."


def plan_payload(plan: list[dict], courses: dict[str, list[dict]]) -> tuple[list[dict], dict[str, dict]]:
    """What the model sees for each skill (with an id per candidate) and the lookup id -> course used to verify its answer."""
    skills, by_id, n = [], {}, 0
    for b in plan:
        g, cands = b["gap"], []
        for c in courses.get(g["skill"], []):
            n += 1
            by_id[f"c{n}"] = {"skill": g["skill"], "course": c}
            cands.append({"id": f"c{n}", "title": c["title"], "provider": c.get("provider"), "kind": "video" if c.get("kind") == "video" else "course",
                          "duration_hours": c.get("duration_hours"), "level": c.get("level"), "is_sample": bool(c.get("is_sample"))})
        skills.append({"skill": g["skill"], "current_level": g["current"], "target_level": g["target"], "planned_hours": b["hours"],
                       "related_skill_already_known": g.get("related_skill"), "candidates": cands})
    return skills, by_id


def verify_picks(raw: object, plan: list[dict], courses: dict[str, list[dict]], by_id: dict[str, dict]) -> tuple[dict, list[str]]:
    """Check the model's choices. -> ({skill: {study, watch, why, practice}} with only the accepted parts, notes about rejected parts).
    A pick is accepted only if: the skill is one of the plan's skills; the course id is a candidate OF THAT SKILL and of the right kind
    (study = course, watch = video); it is not sample data while a real one exists; a study course is not longer than the planned hours
    while a shorter one exists. Text must be one short plain sentence (see _clean_text). Everything else is replaced by the rule-based pick."""
    notes: list[str] = []
    rows = raw.get("skills") if isinstance(raw, dict) else None
    if not isinstance(rows, list):
        return {}, ["the model did not return a list of skills"]
    hours = {b["gap"]["skill"]: b["hours"] for b in plan}
    out: dict[str, dict] = {}
    for row in rows:
        skill = row.get("skill") if isinstance(row, dict) else None
        if skill not in hours or skill in out:
            notes.append(f"ignored a pick for an unknown or repeated skill: {skill!r}")
            continue
        cs, planned, accepted, study_rejected = courses.get(skill, []), hours[skill], {}, False
        for field, want_video in (("study", False), ("watch", True)):
            cid = row.get(f"{field}_id")
            if not cid:
                continue
            study_rejected = study_rejected or (field == "study" and not want_video)  # cleared below when the pick is accepted
            hit = by_id.get(str(cid))
            c = hit["course"] if hit and hit["skill"] == skill else None
            if c is None or (c.get("kind") == "video") != want_video:
                notes.append(f"{skill}: {field} pick {cid!r} is not a {'video' if want_video else 'course'} candidate for this skill")
                continue
            same_kind = [x for x in cs if (x.get("kind") == "video") == want_video]
            if c.get("is_sample") and any(not x.get("is_sample") for x in same_kind):
                notes.append(f"{skill}: {field} pick is sample data but a real one exists")
                continue
            d = c.get("duration_hours")
            if not want_video and d and d > planned and any(not x.get("duration_hours") or x["duration_hours"] <= planned for x in same_kind):
                notes.append(f"{skill}: {field} pick is longer than the planned hours but a shorter course exists")
                continue
            accepted[field] = c
            if field == "study":
                study_rejected = False
        for field, limit in (("why", 200), ("practice", 220)):
            if field == "why" and study_rejected:  # the reason was written for a course the code replaced
                continue
            text = _clean_text(row.get(field), limit)
            if text:
                accepted[field] = text
            elif row.get(field):
                notes.append(f"{skill}: the {field} text was not accepted (a link, markdown, hours or weeks, or too long)")
        if accepted:
            out[skill] = accepted
    return out, notes


def check_plan_text(markdown: str, gaps: list[dict], plan: list[dict], weeks: int, hours_per_week: int) -> list[str]:
    """Re-check the FACTS of a plan (also after a person edited it): the budget, the skill-gap table, the schedule headings and the hours
    breakdown must agree with the numbers computed from the ratings. Wording is not checked. -> problems (empty = consistent)."""
    problems = []
    total = weeks * hours_per_week
    if f"{weeks}-week plan at about {hours_per_week} h/week ({total} h in total)" not in _section(markdown, "Summary"):
        problems.append(f"the Summary no longer states the budget: {weeks}-week plan at about {hours_per_week} h/week ({total} h in total)")
    rows = {}
    for line in _section(markdown, "Skill Gaps").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if "|" in line and len(cells) >= 4 and cells[0].lower() != "skill" and not re.fullmatch(r"[-:\s]+", cells[0]):
            rows[cells[0]] = cells[1:4]
    for g in gaps:
        if rows.get(g["skill"]) != [str(g["current"]), str(g["target"]), str(g["delta"])]:
            problems.append(f"the Skill Gaps table does not show {g['skill']} as {g['current']} to {g['target']} (gap {g['delta']})")
    for skill in rows:
        if skill not in {g["skill"] for g in gaps}:
            problems.append(f"the Skill Gaps table lists {skill}, which is not a gap against the selected requirements")
    for b in plan:
        g = b["gap"]
        heading = f"### {_weeks_label(b['start_week'], b['end_week'])}: {g['skill']} ({g['current']} → {g['target']}) — about {b['hours']:g} h"
        if heading not in markdown:
            problems.append(f"the Weekly Plan no longer has the computed block: {heading[4:]}")
    planned = {b["gap"]["skill"]: b["hours"] for b in plan}
    for x in parse_hours_breakdown(markdown):
        if x["skill"] == "Total":
            continue
        if x["skill"] not in planned:
            problems.append(f"the Hours Breakdown lists {x['skill']}, which has no block in the schedule")
        elif abs(x["planned"] - planned[x["skill"]]) > 0.05:
            problems.append(f"the Hours Breakdown plans {x['planned']:g} h for {x['skill']}, the schedule says {planned[x['skill']]:g} h")
        elif abs(x["course_hours"] + x["practice_hours"] - x["planned"]) > 0.15:
            problems.append(f"the Hours Breakdown for {x['skill']}: {x['course_hours']:g} h courses + {x['practice_hours']:g} h practice is not {x['planned']:g} h")
    return problems


def template_plan(name: str, gaps: list[dict], courses: dict[str, list[dict]], weeks: int, hours: int, project: str,
                  picks: dict | None = None) -> str:
    """The whole plan, built from data. `picks` = the model's choices that verify_picks accepted (none = rule-based plan)."""
    plan = allocate_hours(gaps, weeks, hours)
    breakdown = hours_breakdown(plan, courses, picks)
    title = short_title(project)
    related = any(g.get("related_skill") for g in gaps)
    lines = [f"# Learning Path — {name}", "", "## Summary", "",
             f"{weeks}-week plan at about {hours} h/week ({weeks * hours} h in total)" + (f" for **{title}**" if title else "")
             + f", closing {len(gaps)} skill gap(s). The hours are split in proportion to the size of each gap, so equal gaps get equal time.", "",
             "## Skill Gaps", "", "| Skill | Current | Target | Gap |" + (" Related skill you have |" if related else ""),
             "|---|---|---|---|" + ("---|" if related else "")]
    for g in gaps:
        rel = f" {g['related_skill']} ({g['related_rating']}/4) |" if g.get("related_skill") else (" — |" if related else "")
        lines.append(f"| {g['skill']} | {g['current']} | {g['target']} | {g['delta']} |{rel}")
    lines += ["", "## Weekly Plan", "", "A week can hold the end of one skill and the start of the next.", ""]
    shortfalls = []
    for b in plan:
        g = b["gap"]
        lines.append(f"### {_weeks_label(b['start_week'], b['end_week'])}: {g['skill']} ({g['current']} → {g['target']}) — about {b['hours']:g} h")
        cs = courses.get(g["skill"], [])
        study, watch = _resources(g["skill"], b["hours"], g["target"], courses, picks)
        lines.append(f"- Study: [{study['title']}]({study['url']})" + (f" ({study['provider']})" if study.get("provider") else "")
                     if study else "- Study: no course found; agree a resource with your lead.")
        if watch:
            counted = next((x["counted"] for x in breakdown if x["skill"] == g["skill"]), [])
            optional = bool(watch.get("duration_hours")) and not any(c.startswith(f"{watch['title']} (") for c in counted)
            lines.append(f"- Watch: [{watch['title']}]({watch['url']}) ({watch.get('provider') or 'video'})"
                         + (" — optional, not counted in the hours" if optional else ""))
        if study and study.get("duration_hours") and study["duration_hours"] > b["hours"]:
            shortfalls.append(g["skill"])
            lines.append(f"- Time check: this course takes about {study['duration_hours']:g} h but only about {b['hours']:g} h are planned. "
                         "Take the core modules or add time.")
        if g.get("related_skill"):
            lines.append(f"- Builds on: {g['related_skill']} ({g['related_rating']}/4), which you already have. Use it for the practice task.")
        note = (picks or {}).get(g["skill"], {})
        if note.get("why"):
            lines.append(f"- Why this choice: {note['why']}")
        lines.append(f"- Practice: {note['practice']}" if note.get("practice")
                     else f"- Practice: apply {g['skill']} to a small real task from the project backlog.")
        lines.append(f"- Check: demonstrate {g['skill']} at level {g['target']} to your lead.")
        lines.append("")
    if len(plan) < len(gaps):
        lines += [f"> {len(gaps) - len(plan)} smaller gap(s) do not fit in {weeks * hours} hours; schedule a follow-up plan.", ""]
    chosen = set()
    for b in plan:
        for c in _resources(b["gap"]["skill"], b["hours"], b["gap"]["target"], courses, picks):
            if c:
                chosen.add((c["url"], c["title"]))
    lines += ["## Courses", "", "Courses marked *in this plan* are the ones scheduled above and counted in the Hours Breakdown; "
              "the others are alternatives and are not counted.", ""]
    seen = set()
    for skill, cs in courses.items():
        for c in cs:
            if (c["url"], c["title"]) in seen:
                continue
            seen.add((c["url"], c["title"]))
            meta = ", ".join(x for x in [c.get("provider") or None, "video" if c.get("kind") == "video" else None,
                                         f"{c['duration_hours']:g}h" if c.get("duration_hours") else None,
                                         f"★{c['rating']}" if c.get("rating") else None, c.get("level")] if x)
            tag = " *(sample data)*" if c.get("is_sample") else ""
            use = " — *in this plan*" if (c["url"], c["title"]) in chosen else " — alternative"
            lines.append(f"- **{skill}**: [{c['title']}]({c['url']}) ({meta}){tag}{use}")
    if not seen:
        lines.append("- No courses were retrieved.")
    lines += ["", "## Hours Breakdown", "", hours_section(breakdown), "", "## Milestones & Checks", ""]
    for b in plan:  # one milestone per skill, on the last week of its block
        lines.append(f"- End of week {b['end_week']}: demonstrate {b['gap']['skill']} at level {b['gap']['target']} to your lead.")
    lines += [f"- End of week {weeks}: re-rate the skills above and update the skill matrix.", ""]
    if shortfalls:
        lines.insert(lines.index("## Weekly Plan"), f"> Time check: the first suggested course for {', '.join(shortfalls)} is longer than the hours planned for it.\n")
    return "\n".join(lines)


def _learning_gaps(emp: dict, requirements: list) -> list[dict]:
    have = {s["canonical_skill"]: (s["rating"] or 0) for s in emp["skills"]}
    for requirement in requirements:
        have[requirement.skill] = groups.effective(have, requirement.skill)[0]
    return sorted(
        ({"skill": requirement.skill, "current": have.get(requirement.skill, 0), "target": requirement.level,
          "delta": requirement.level - have.get(requirement.skill, 0)}
         for requirement in requirements if have.get(requirement.skill, 0) < requirement.level),
        key=lambda gap: -gap["delta"],
    )


def _met_requirements(emp: dict, requirements: list) -> list[dict]:
    """The requested targets the person ALREADY meets (the other side of _learning_gaps): [{skill, current, target}]."""
    have = {s["canonical_skill"]: (s["rating"] or 0) for s in emp["skills"]}
    for requirement in requirements:
        have[requirement.skill] = groups.effective(have, requirement.skill)[0]
    return [{"skill": r.skill, "current": have.get(r.skill, 0), "target": r.level}
            for r in requirements if have.get(r.skill, 0) >= r.level]


def _met_text(met: list[dict]) -> str:
    """'Agile/Scrum: current level 4 (Mastered), target level 3 (Experienced), already met' for each met target."""
    return "; ".join(f"{m['skill']}: current level {m['current']} ({LEVEL_NAMES.get(m['current'], '')}), target level {m['target']} "
                     f"({LEVEL_NAMES.get(m['target'], '')}), already met" for m in met)


def allowed_advice(allocations: list[dict], breakdown: list[dict], plan_markdown: str, weeks: int) -> list[str]:
    """The ONLY advice the coach may give, built by code from the plan on screen. Anything else is 'not in the plan: ask your lead'."""
    advice = []
    if len(allocations) > 1:
        top = max(allocations, key=lambda b: b["hours"])
        advice.append(f"Start with {top['gap']['skill']}: it has the largest gap and the most planned hours ({top['hours']:g} h).")
    skill = None
    for line in plan_markdown.splitlines():
        head = re.match(r"^### .+?: (.+?) \(\d+ → \d+\)", line)
        if head:
            skill = head.group(1)
        elif skill and line.startswith("- Practice: "):
            advice.append(f"For {skill} the plan's practice task is: {line[len('- Practice: '):].strip()}")
        elif skill and line.startswith("- Check: "):
            advice.append(f"For {skill} the plan's check is: {line[len('- Check: '):].strip()}")
    advice += [f"At the end of week {weeks}, re-rate the skills and update the skill matrix (a milestone in the plan).",
               "If time was missed or the pace does not fit, ask your lead to revise the plan; the computed schedule stays as it is until then.",
               "Study time alone does not guarantee a level: practical work and reassessment confirm progress."]
    return advice[:12]


def coach(req: LearningCoachRequest) -> dict:
    if req.messages[-1].role != "user":
        raise GuardrailViolation("Send a learner message before asking the coach to respond.")

    messages = normalize_messages(req.messages)
    if not messages or messages[-1]["role"] != "user":
        raise GuardrailViolation("Send a learner message before asking the coach to respond.")
    # Only the NEW message must pass the guardrails (a refusal is shown to the person). Earlier turns were checked when they were asked: one that
    # was refused then is simply left out of the conversation now, so it can neither be refused again on every later turn nor reach the model.
    checked = []
    for i, message in enumerate(messages):
        if message["role"] != "user":
            checked.append(message)
        elif i == len(messages) - 1:
            checked.append({"role": "user", "content": check_input(message["content"])})
        else:
            try:
                checked.append({"role": "user", "content": check_input(message["content"])})
            except GuardrailViolation:
                continue
    messages = checked
    cached_reply = cached_conversational_reply(messages[-1]["content"], "learning_coach")
    if cached_reply:
        return {"answer": cached_reply, "warnings": []}
    emp = repo.get_employee(req.employee_id)
    if not emp:
        raise LookupError("Employee not found")
    gaps = _learning_gaps(emp, req.requirements)
    allocations = allocate_hours(gaps, req.weeks, req.hours_per_week)
    breakdown = parse_hours_breakdown(req.plan_markdown)  # the computed split of the plan on screen; empty for plans made before it existed
    met = _met_requirements(emp, req.requirements)
    verified = {
        "employee_profile": eval_log.profile_text(emp),
        "time_budget": {"weeks": req.weeks, "hours_per_week": req.hours_per_week,
                        "total_hours": req.weeks * req.hours_per_week},
        "requested_targets": [f"{r.skill}: level {r.level} ({LEVEL_NAMES.get(r.level, '')})" for r in req.requirements],
        "targets_already_met": [f"{m['skill']}: current level {m['current']} ({LEVEL_NAMES.get(m['current'], '')}), target level {m['target']}"
                                for m in met],
        "skill_gaps": gaps,
        "computed_schedule": time_plan_lines(allocations),
        "hours_breakdown": breakdown,
        "allowed_advice": allowed_advice(allocations, breakdown, req.plan_markdown, req.weeks),
        "current_plan": req.plan_markdown,
    }
    context = [
        verified["employee_profile"],
        (f"Verified budget: {req.weeks} weeks, {req.hours_per_week} hours per week, "
         f"{req.weeks * req.hours_per_week} total hours."),
        "Requested targets: " + ("; ".join(verified["requested_targets"]) or "none."),
        "Targets the person already meets (no study time is planned for them): " + (_met_text(met) or "none."),
        "Verified skill gaps: " + ("; ".join(
            f"{gap['skill']} level {gap['current']} to {gap['target']} (gap {gap['delta']})" for gap in gaps
        ) or "none against the selected requirements."),
        "Authoritative computed schedule: " + ("; ".join(verified["computed_schedule"]) or "no schedule is needed."),
        "Verified hours breakdown (planned; courses; practice and review): " + ("; ".join(
            f"{x['skill']}: {x['planned']:g} h; {x['course_hours']:g} h; {x['practice_hours']:g} h" for x in breakdown)
            or "the plan does not state how its hours split between courses and practice."),
        "Allowed advice (the only advice the coach may give, built from the plan): " + " ".join(verified["allowed_advice"]),
        "Current learning plan (provided by the app page):\n" + req.plan_markdown,
    ]
    if len(messages) > 1:  # only to understand a follow-up; not evidence
        context.append("Earlier conversation, used only to understand the latest message (not evidence): " + " ".join(
            f"{'Learner' if m['role'] == 'user' else 'Coach'}: {str(m['content'])[:500]}" for m in messages[-7:-1]))
    answer = llm.complete(
        prompts.LEARNING_COACH_SYSTEM,
        json.dumps({"verified_context": verified, "conversation": messages}, ensure_ascii=False),
        namespace="learning_coach",
    )
    answer, warnings = check_output(answer)
    if not answer.strip():
        raise RuntimeError("The learning coach returned an empty answer.")
    allowed_hours = {float(req.weeks * req.hours_per_week), float(req.hours_per_week)} | {float(b["hours"]) for b in allocations}
    for x in breakdown:
        allowed_hours |= {x["planned"], x["course_hours"], x["practice_hours"]}
    allowed_hours |= {float(n) for n in re.findall(r"(\d+(?:\.\d+)?)\s*(?:h|hrs?|hours?)\b", req.plan_markdown)}
    meta = {"kind": "learning_coach", "employee_id": req.employee_id, "weeks": req.weeks, "hours_per_week": req.hours_per_week,
            "hours_breakdown": breakdown, "allowed_hours": sorted(allowed_hours), "data_fingerprint": eval_log.data_fingerprint()}
    eval_log.log_interaction("learning_coach", messages[-1]["content"], answer, context, meta=meta)  # question = the latest message
    return {"answer": answer, "warnings": warnings}


# ---------------------------------------------------------------- notes in plain words
# The course search and the AI steps report what they did in technical terms. The person reading the plan gets a short, friendly sentence
# instead: no tool names, no setting names, no "(s)" and no error text. The technical detail goes to the server log only.
_EXCLUDED = re.compile(r"excluded\s+(\d+)\s+(course|video|link|resource)s?(?:\(s\))?\s+that\s+(?:did\s+not|didn.?t)\s+appear\s+to\s+(?:teach|cover|be\s+about)\s+(.+?)\.?\s*$", re.I)
_TECHNICAL = re.compile(r"[A-Z][A-Z0-9_]{3,}=|Error\b|Exception\b|Traceback|\bNone\b|[{}]|\bkey\b|\btoken\b", re.I)


def _count_word(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def course_note_in_words(skill: str, note: str) -> tuple[str, str]:
    """A note from the course search -> ("notice" | "warning", a sentence a person can read)."""
    m = _EXCLUDED.search(note or "")
    if m:
        n, kind, what = int(m.group(1)), m.group(2).lower(), m.group(3).strip() or skill
        return "notice", (f"I double-checked the {kind} suggestions for {what} and set aside {_count_word(n, kind)} that "
                          f"{'doesn’t' if n == 1 else 'don’t'} seem to be about it, so what is left stays on topic.")
    if _TECHNICAL.search(note or ""):
        log.info("Course note for %s not shown as written: %s", skill, note)
        return "warning", f"I had some trouble checking the course suggestions for {skill}, so you may see fewer options there."
    return "warning", f"About {skill}: {note}"


def plain_warning(text: str) -> str:
    """A warning from the AI steps (guard notes and the like), with technical wording replaced by a plain sentence."""
    if _TECHNICAL.search(text or ""):
        log.info("Plan warning not shown as written: %s", text)
        return "I changed a few words in the plan to keep it appropriate."
    return text


def build(req: LearningRequest, require_approval: bool = False) -> dict:
    emp = repo.get_employee(req.employee_id)
    if not emp:
        raise LookupError("Employee not found")
    project = check_input(req.project)
    gaps = _learning_gaps(emp, req.requirements)
    if not gaps:
        met = _met_requirements(emp, req.requirements)
        msg = (f"# Learning Path — {emp['name']}\n\nNo skill gaps against the selected requirements."
               + (f" Already met: {_met_text(met)}." if met else "")
               if req.requirements else f"# Learning Path — {emp['name']}\n\nNo required skills were provided, so there is nothing to plan.")
        return {"markdown": msg, "mode": "none", "warnings": [], "gaps": []}

    _related_skills(emp, gaps)
    plan = allocate_hours(gaps, req.weeks, req.hours_per_week)
    breakdown: list[dict] = []
    warnings: list[str] = []
    coach_notices: list[str] = []
    courses: dict[str, list[dict]] = {}
    for g in gaps:
        try:
            found, notes = search_courses(g["skill"], _level(g["target"]), limit=4)
            courses[g["skill"]] = [c.model_dump() for c in found]
            for note in notes:
                kind, text = course_note_in_words(g["skill"], note)
                target = coach_notices if kind == "notice" else warnings
                if text not in target:
                    target.append(text)
        except Exception as e:  # noqa: BLE001  a course source outage must not break the plan
            log.warning("Course search failed for %s: %s", g["skill"], e)
            courses[g["skill"]] = []
            warnings.append(f"I couldn’t look up courses for {g['skill']} just now, so there are no course links for it yet. "
                            "Try generating the plan again in a moment.")
    if any(c.get("is_sample") for cs in courses.values() for c in cs):
        warnings.append("The course links in this plan are examples, so they open a search page rather than a specific course.")

    mode, picks, notes, review_notes = "template", {}, [], []
    if llm.is_configured():
        try:  # the model chooses; verify_picks decides what is kept
            skills, by_id = plan_payload(plan, courses)
            out = orchestration.run_learning_path({
                "name": emp["name"], "roles": ", ".join(emp["roles"]) or "n/a", "background": (emp["background"] or "")[:1500],
                "project": project, "skills_json": json.dumps(skills, ensure_ascii=False), "warnings": [],
            })
            picks, notes = verify_picks(out.get("picks"), plan, courses, by_id)
            review_notes = [str(x)[:200] for x in (out.get("review") or [])][:4]  # what the AI reviewer changed in the draft (code verified the result)
            warnings += [plain_warning(w) for w in out.get("warnings", [])]
            mode = "llm" if picks else "template"
            if not picks:
                warnings.append("I couldn’t use the AI’s course picks this time, so you’re seeing the standard plan instead.")
        except Exception as e:  # noqa: BLE001
            log.warning("LLM planning step failed, using the rule-based plan: %s", e, exc_info=True)
            warnings.append("I couldn’t use the AI planner this time, so you’re seeing the standard plan instead. Generating again may fix it.")
            picks, notes, mode = {}, [], "template"
    if notes:
        log.info("Learning plan: model choices replaced by the rules: %s", "; ".join(notes))
    breakdown = hours_breakdown(plan, courses, picks)
    markdown = template_plan(emp["name"], gaps, courses, req.weeks, req.hours_per_week, project, picks)
    problems = check_plan_text(markdown, gaps, plan, req.weeks, req.hours_per_week)  # the finished plan must agree with the computed numbers
    if problems:
        log.error("Built plan fails its own fact check, falling back to the rule-based plan: %s", "; ".join(problems))
        picks, mode = {}, "template"
        breakdown = hours_breakdown(plan, courses)
        markdown = template_plan(emp["name"], gaps, courses, req.weeks, req.hours_per_week, project)

    _log_plan(emp, gaps, courses, req, markdown, mode, plan, warnings, breakdown, picks, notes, project)
    result = {"markdown": markdown, "mode": mode, "warnings": warnings, "gaps": gaps, "coach_notices": coach_notices,
              "review_notes": review_notes if mode == "llm" else []}
    if require_approval and mode == "llm":  # a person chooses between the AI-planned plan and the plain rule-based one
        result.update({
            "approval_required": True,
            "template_markdown": template_plan(emp["name"], gaps, courses, req.weeks, req.hours_per_week, project),
            "computed_budget": f"{req.weeks} weeks × {req.hours_per_week} hours/week = {req.weeks * req.hours_per_week} hours",
            "computed_schedule": time_plan_lines(plan),
        })
    else:
        result["approval_required"] = False
    return result


def check_edited_plan(req: LearningPlanCheckRequest) -> dict:
    """Re-check the facts of a plan after a manager, HR or admin edited it. The edit is never blocked; the problems are shown."""
    emp = repo.get_employee(req.employee_id)
    if not emp:
        raise LookupError("Employee not found")
    gaps = _learning_gaps(emp, req.requirements)
    plan = allocate_hours(gaps, req.weeks, req.hours_per_week) if gaps else []
    problems = check_plan_text(req.plan_markdown, gaps, plan, req.weeks, req.hours_per_week) if gaps else []
    return {"ok": not problems, "problems": problems}


def _log_plan(emp: dict, gaps: list[dict], courses: dict[str, list[dict]], req: LearningRequest, markdown: str, mode: str,
              plan: list[dict], warnings: list[str] | None = None, breakdown: list[dict] | None = None,
              picks: dict | None = None, notes: list[str] | None = None, project: str = "") -> None:
    """Record the plan for RAGAS (question, answer, contexts, derived reference). Never raises."""
    try:
        question = (f"Create a {req.weeks}-week study plan (about {req.hours_per_week} hours per week) for {emp['name']} to reach "
                    + ", ".join(f"level {g['target']} in {g['skill']}" for g in gaps) + ".")
        lines = [f"Retrieved course for {skill}: {json.dumps(course, ensure_ascii=False)}"
                 for skill, items in courses.items() for course in items]
        total = req.weeks * req.hours_per_week
        gap_text = "; ".join(f"{g['skill']} from level {g['current']} to {g['target']}" for g in gaps)
        split = "; ".join(f"{b['gap']['skill']} about {b['hours']:g} h ({_weeks_label(b['start_week'], b['end_week'])})" for b in plan)
        # contexts carry the facts the plan quotes: the request, the gaps with current and target levels, and the time split
        facts = [f"Request: {req.weeks} weeks at {req.hours_per_week} hours per week ({total} hours in total) for {emp['name']}.",
                 f"Skill gaps (current level to target level): {gap_text}.",
                 f"Time split in proportion to gap size: {split}."]
        if breakdown:  # backs the Hours Breakdown section of the plan
            facts.append("Hours breakdown (planned; courses; practice and review): " + "; ".join(
                f"{x['skill']}: {x['planned']:g} h; {x['course_hours']:g} h; {x['practice_hours']:g} h" if x["known"]
                else f"{x['skill']}: {x['planned']:g} h; course lengths not listed" for x in breakdown) + ".")
        reference = f"Skill gaps: {gap_text}. The {total} hours ({req.weeks} weeks at {req.hours_per_week} hours per week) are split in proportion to gap size: {split}."
        if mode in ("template", "llm"):  # the study course and the video are known: the accepted model pick, otherwise the rule-based pick
            picks = []
            hours_of = {b["gap"]["skill"]: b["hours"] for b in plan}
            for g in gaps:
                cs = courses.get(g["skill"], [])
                study, watch = _resources(g["skill"], hours_of.get(g["skill"], 0), g["target"], courses, picks)
                if study:
                    picks.append(f"{g['skill']}: study \"{study['title']}\"" + (f" and watch \"{watch['title']}\"" if watch else ""))
            if picks:
                reference += " Recommended resources from the retrieved courses: " + "; ".join(picks) + "."
        meta = {"mode": mode, "employee_id": emp["id"], "weeks": req.weeks, "hours_per_week": req.hours_per_week,
                "requested": [{"skill": r.skill, "target": r.level} for r in req.requirements],
                "gaps": [{"skill": g["skill"], "current": g["current"], "target": g["target"], "delta": g["delta"]} for g in gaps],
                "plan": [{"skill": b["gap"]["skill"], "hours": b["hours"], "start_week": b["start_week"], "end_week": b["end_week"]} for b in plan],
                "hours_breakdown": [{k: x[k] for k in ("skill", "planned", "course_hours", "practice_hours", "known", "over")} for x in (breakdown or [])],
                "data_fingerprint": eval_log.data_fingerprint(), "warnings": (warnings or [])[:10], "model_picks_rejected": (notes or [])[:10]}
        wording = ["How the study plan is written: every skill gets a study block (one retrieved course), a video to watch, a practice task "
                   "(apply the skill to a small real task from the project backlog, unless a more specific task is given), a check (demonstrate the "
                   "skill at the target level to your lead) and a time check that compares the course length with the planned hours. A week can "
                   "hold the end of one skill and the start of the next. The plan ends with a milestone: at the end of the last week, re-rate the "
                   "skills and update the skill matrix. Course time is counted against each skill's hours; the rest is hands-on practice and "
                   "review. Courses marked in this plan are the scheduled ones counted in the Hours Breakdown; the others are alternatives. "
                   "A study course, video and practice task may be chosen by the AI from the retrieved courses and the person's background; the "
                   "app checks each choice and replaces one that is not a retrieved course for that skill."]
        if project.strip():
            wording.append(f"Project or goal given for this plan: {project.strip()[:600]}")
        eval_log.log_interaction("learning_path", question, markdown, [eval_log.profile_text(emp)] + facts + lines + wording, reference, meta)
    except Exception:  # noqa: BLE001
        log.exception("Could not log the learning plan")
