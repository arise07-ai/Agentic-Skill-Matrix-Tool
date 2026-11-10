"""Build the RAGAS evaluation dataset (eval_dataset.json) from the answers the app has logged.

Every staffing, learning-path, learning-coach and Ask-the-matrix (chat) answer is logged to backend/data/eval/interactions.jsonl (see ai/eval_log.py). This turns
that log into the dataset file that ai/evaluation.py scores:

    python -m app.ai.make_eval_dataset                      # log -> ../samples/eval_dataset.json
    python -m app.ai.make_eval_dataset out.json             # choose the output file
    python -m app.ai.make_eval_dataset --replay             # first run sample demands through the real staffing /
                                                            # learning code (which logs them), then build the dataset
    python -m app.ai.make_eval_dataset --all                # keep repeated questions (default: latest answer per question)
    python -m app.ai.make_eval_dataset --refresh-references # refresh optional reference answers
    python -m app.ai.make_eval_dataset --archive-old-log    # move log rows in the old format into a dated archive file
    python -m app.ai.make_eval_dataset --include-old        # also use log rows in the old format (not recommended)

Rows logged by an older version of the app are skipped (their contexts lack the evidence for the answer's claims, which
makes RAGAS scores misleading). The command lists the questions to ask again in the app.

Each row: {"question", "answer", "contexts", "ground_truth", "meta"}.
  ground_truth  is an optional reference retained for review and compatibility; the three RAGAS metrics do not use it.
                If the output file already exists, references you edited there are kept.
"""
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

from .. import db
from ..schemas import LearningRequest, ProjectInput, Requirement
from ..services import learning, projects, repo
from . import eval_log

DEFAULT_OUT = Path(__file__).resolve().parents[3] / "samples" / "eval_dataset.json"


def _demands(emps: list[dict], n: int) -> list[tuple[str, list[str]]]:
    """One demand per common role: the role's two most frequent skills (so the demand can be satisfied)."""
    roles = Counter(r for e in emps for r in e["roles"])
    out = []
    for role, _ in roles.most_common(n):
        freq = Counter(name for e in emps if role in e["roles"] for name in e["skills"])
        top = [s for s, _ in freq.most_common(2)]
        if len(top) == 2:
            out.append((role, top))
    return out


def replay(n_staffing: int = 6, n_learning: int = 6) -> int:
    """Run sample demands through the real code paths. Staffing and learning paths log themselves."""
    emps = repo.all_profiles()
    if not emps:
        raise SystemExit("No employees found. Import your Excel/PDF in the app first.")
    done = 0
    demands = _demands(emps, max(n_staffing, n_learning, 12))
    for role, skills in demands[:n_staffing]:
        reqs = [Requirement(skill=s, level=3) for s in skills]
        projects.staffing(ProjectInput(prompt=f"Who should staff a project that needs a {role} with {skills[0]} and {skills[1]} at level 3 or higher?",
                                       requirements=reqs, headcount=3, top_n=5))
        done += 1
    by_role = dict(demands)
    for e in emps:
        if done >= n_staffing + n_learning:
            break
        role = next((r for r in e["roles"] if r in by_role), None)
        if not role:
            continue
        have = {name: v["rating"] or 0 for name, v in e["skills"].items()}
        reqs = [Requirement(skill=s, level=4) for s in by_role[role] if have.get(s, 0) < 4]
        if reqs:
            learning.build(LearningRequest(employee_id=e["id"], requirements=reqs, project=f"{role} upskilling", weeks=4, hours_per_week=5))
            done += 1
    return done


def _h(text: str) -> str:
    return hashlib.md5((text or "").encode("utf-8")).hexdigest()[:12]


def from_log(keep_all: bool = False, include_old: bool = False) -> tuple[list[dict], list[str]]:
    """-> (dataset rows, questions that exist only in the old format and should be asked again)."""
    rows = eval_log.read_log()
    new, old = eval_log.split_by_schema(rows)
    if not include_old:
        asked_again = {r["question"] for r in new}
        stale = list(dict.fromkeys(r["question"] for r in old if r["question"] not in asked_again))
        rows = new
    else:
        stale = []
    if not keep_all:  # latest answer per question
        latest: dict[str, dict] = {}
        for r in rows:
            latest[r["question"]] = r
        rows = list(latest.values())
    return [{"question": r["question"], "answer": r["answer"], "contexts": r["contexts"], "ground_truth": r.get("reference", ""),
             # reference_hash = fingerprint of the reference the app derived; a person's edit is recognised by a different text
             "meta": {"kind": r.get("kind"), "ts": r.get("ts"), "reference_hash": _h(r.get("reference", "")), **(r.get("meta") or {})}}
            for r in rows], stale


def keep_edited_references(rows: list[dict], target: Path) -> tuple[int, int]:
    """References a person edited in the existing dataset file win over freshly derived ones.
    An edit is recognised by comparing the old text with the hash of what the app had derived. Old files without that hash
    keep their reference as before (we cannot tell). -> (edited kept, legacy kept)"""
    if not target.exists():
        return 0, 0
    try:
        old = {r["question"]: r for r in json.loads(target.read_text(encoding="utf-8"))}
    except (ValueError, KeyError, TypeError):
        return 0, 0
    edited = legacy = 0
    for r in rows:
        prev = old.get(r["question"])
        if not prev or not prev.get("ground_truth") or prev["ground_truth"] == r["ground_truth"]:
            continue
        derived_hash = (prev.get("meta") or {}).get("reference_hash")
        if derived_hash is None:
            r["ground_truth"] = prev["ground_truth"]
            legacy += 1
        elif _h(prev["ground_truth"]) != derived_hash:
            r["ground_truth"] = prev["ground_truth"]
            edited += 1
    return edited, legacy


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    target = Path(args[0]) if args else DEFAULT_OUT
    db.init_db()
    if "--archive-old-log" in flags:
        n, where = eval_log.archive_old_rows()
        print(f"Moved {n} old-format log row(s) to {where}" if n else "No old-format rows in the log.")
    if "--replay" in flags:
        print(f"Replayed {replay()} sample requests through the app code.")
    data, stale = from_log(keep_all="--all" in flags, include_old="--include-old" in flags)
    if stale:
        print(f"\n{len(stale)} question(s) exist only in the old log format and were SKIPPED. Ask them again in the app to include them:")
        for q in stale:
            print("  -", q[:160] + ("…" if len(q) > 160 else ""))
        print()
    if not data:
        raise SystemExit("No rows in the current format. Use Staffing / Learning paths in the app, or add --replay to generate sample requests.")
    edited = legacy = 0
    if "--refresh-references" not in flags:
        edited, legacy = keep_edited_references(data, target)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    kinds = Counter(r["meta"].get("kind") for r in data)
    print(f"Wrote {len(data)} rows to {target} ({dict(kinds)}); kept {edited} reference answer(s) edited by a person.")
    if legacy:
        print(f"{legacy} reference answer(s) come from an older dataset file and could not be checked for edits, so they were kept. "
              "If you never edited them, run once with --refresh-references to use the newly derived ones.")
