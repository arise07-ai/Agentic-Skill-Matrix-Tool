"""Edits YOUR app/ai/evaluation.py in place.  Run from the backend folder:   python apply_eval_context_fix.py
The text it replaces must appear exactly once; otherwise nothing is written. Backup: evaluation.py.bak"""
import shutil
import sys
from pathlib import Path

path = Path(__file__).resolve().parent / "app" / "ai" / "evaluation.py"
OLD = '''                have = {x["canonical_skill"]: (x["rating"] or 0) for x in emp_full["skills"]}
                checked = "Requirements checked against this person's ratings: " + "; ".join(
                    f"{r['skill']} target {r['level']}, rated {have.get(r['skill'], 0) or 'none'}" for r in inp["requirements"]) + "."
'''
NEW = '''                from ..services.seniority import LEVEL_NAMES
                have = {x["canonical_skill"]: (x["rating"] or 0) for x in emp_full["skills"]}

                def _lv(n):  # the judge sees the level NAME next to the number, so a plan that says "Mastered" is backed by the context
                    return f"{n} ({LEVEL_NAMES.get(n, '')})"
                checked = ("Requirements checked against this person's ratings (levels: 0 None, 1 Learned, 2 Applied, 3 Experienced, 4 Mastered): "
                           + "; ".join(f"{r['skill']} target level {_lv(r['level'])}, rated level {_lv(have.get(r['skill'], 0))}"
                                       + (", target already met" if have.get(r['skill'], 0) >= r['level'] else ", gap")
                                       for r in inp["requirements"])
                           + ". A requirement whose rating is at or above its target is not a skill gap.")
'''
if not path.exists():
    sys.exit(f"STOP: {path} not found. Run this from the backend folder (the one that contains app/).")
s = path.read_text(encoding="utf-8")
if "A requirement whose rating is at or above its target" in s:
    sys.exit("STOP: this fix is already applied. Nothing changed.")
if s.count(OLD) != 1:
    sys.exit(f"STOP: expected text found {s.count(OLD)} time(s), not once. Nothing changed. Send me your current evaluation.py.")
shutil.copy2(path, str(path) + ".bak")
path.write_text(s.replace(OLD, NEW), encoding="utf-8")
print("updated app/ai/evaluation.py (backup: evaluation.py.bak)")
