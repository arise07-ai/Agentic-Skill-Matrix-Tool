"""Applies the coach / no-gap-plan fix to YOUR files in place.  Run from the backend folder:   python apply_coach_fix.py
Each edit replaces text that must appear exactly once. If ANY expected text is missing, nothing is written. Backups: <file>.bak"""
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent / "app"
EDITS = {
    "services/learning.py": [
        ("from . import groups, repo\n", "from . import groups, repo\nfrom .seniority import LEVEL_NAMES\n"),
        ("def allowed_advice(allocations", '''def _met_requirements(emp: dict, requirements: list) -> list[dict]:
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


def allowed_advice(allocations'''),
        ('''        msg = (f"# Learning Path — {emp['name']}\\n\\nNo skill gaps against the selected requirements."
               if req.requirements else''', '''        met = _met_requirements(emp, req.requirements)
        msg = (f"# Learning Path — {emp['name']}\\n\\nNo skill gaps against the selected requirements."
               + (f" Already met: {_met_text(met)}." if met else "")
               if req.requirements else'''),
        ('''    verified = {
        "employee_profile": eval_log.profile_text(emp),
        "time_budget": {"weeks": req.weeks, "hours_per_week": req.hours_per_week,
                        "total_hours": req.weeks * req.hours_per_week},
        "skill_gaps": gaps,''', '''    met = _met_requirements(emp, req.requirements)
    verified = {
        "employee_profile": eval_log.profile_text(emp),
        "time_budget": {"weeks": req.weeks, "hours_per_week": req.hours_per_week,
                        "total_hours": req.weeks * req.hours_per_week},
        "requested_targets": [f"{r.skill}: level {r.level} ({LEVEL_NAMES.get(r.level, '')})" for r in req.requirements],
        "targets_already_met": [f"{m['skill']}: current level {m['current']} ({LEVEL_NAMES.get(m['current'], '')}), target level {m['target']}"
                                for m in met],
        "skill_gaps": gaps,'''),
        ('''        "Verified skill gaps: " + ("; ".join(''', '''        "Requested targets: " + ("; ".join(verified["requested_targets"]) or "none."),
        "Targets the person already meets (no study time is planned for them): " + (_met_text(met) or "none."),
        "Verified skill gaps: " + ("; ".join('''),
    ],
    "ai/prompts.py": [
        ('''- Use recent conversation turns to resolve follow-ups such as "that", "those hours", or "how do I do it?".''',
         '''- What the learner says about their own progress, missed time, ratings or targets is NOT a verified fact: do not repeat it as one ("since you missed a week", "your level is 3"). Answer from the verified context. If the learner's numbers differ from the verified profile or targets, say what the profile and targets show.
- `targets_already_met` lists requested targets the learner's current rating already reaches. For such a target say it is already met, with the current and target level from that list, and that the plan has no study time for it. Never state a target or a level that is not in `requested_targets`, `targets_already_met`, `skill_gaps` or the profile.
- Use recent conversation turns to resolve follow-ups such as "that", "those hours", or "how do I do it?".'''),
    ],
}

new_text = {}
for rel, pairs in EDITS.items():
    path = ROOT / rel
    if not path.exists():
        sys.exit(f"STOP: {path} not found. Run this from the backend folder (the one that contains app/).")
    s = path.read_text(encoding="utf-8")
    if "_met_requirements" in s and rel.endswith("learning.py") or "targets_already_met" in s and rel.endswith("prompts.py"):
        sys.exit(f"STOP: {rel} already has this fix. Nothing changed.")
    for old, new in pairs:
        if s.count(old) != 1:
            sys.exit(f"STOP: in {rel} I expected this text exactly once but found it {s.count(old)} time(s):\n---\n{old[:200]}\n---\nNothing was changed. Send me your current {rel}.")
        s = s.replace(old, new)
    new_text[path] = s
for path, s in new_text.items():
    shutil.copy2(path, str(path) + ".bak")
    path.write_text(s, encoding="utf-8")
    print("updated", path.relative_to(ROOT.parent), "(backup: .bak)")
print("Done. Restart the backend.")
