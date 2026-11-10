"""EVALUATION LOG: every staffing, learning-path, and learning-coach answer the app gives is appended to a JSON-lines file together
with the question, answer, contexts, and a reference answer when available.

    backend/data/eval/interactions.jsonl        (one JSON object per line; local file, never sent anywhere)

Build the evaluation dataset from it, then score it:
    python -m app.ai.make_eval_dataset ../samples/eval_dataset.json
    python -m app.ai.evaluation ../samples/eval_dataset.json

RAGAS does not require or score the stored reference answer; it evaluates answer grounding, answer relevance, and context relevance.
The optional reference is retained for review and compatibility with existing evaluation data.
Turn logging off with EVAL_LOG_ENABLED=false. The log contains employee names and background text: protect the file.
"""
import json
import logging
import threading
import uuid
from datetime import datetime, timezone

from .. import db
from ..config import get_settings

log = logging.getLogger(__name__)
_lock = threading.Lock()
# Version of what a logged row contains. 2 = contexts carry the evidence behind every claim, and meta carries the data that
# ai/eval_checks.py needs. Rows without this number (or lower) are skipped when the dataset is built.
SCHEMA = 2


def profile_text(e: dict) -> str:
    """One employee as a context passage. Accepts both profile shapes used in the app (skills as dict or as list)."""
    sk = e.get("skills") or {}
    items = ([(n, v.get("rating")) for n, v in sk.items()] if isinstance(sk, dict)
             else [(s["canonical_skill"], s.get("rating")) for s in sk])
    skills = ", ".join(f"{n} {r}/4" for n, r in items if r)
    no = f" ({e['employee_no']})" if e.get("employee_no") else ""
    tags = ", ".join(e.get("domain_tags") or []) or "n/a"
    return f"{e['name']}{no}. Roles: {', '.join(e.get('roles') or []) or 'n/a'}. Skills: {skills or 'n/a'}. Domain tags: {tags}. Background: {e.get('background') or 'n/a'}"


def log_interaction(kind: str, question: str, answer: str, contexts: list[str], reference: str = "", meta: dict | None = None) -> None:
    """Append one interaction. Never raises: logging must not break staffing or learning paths."""
    try:
        s = get_settings()
        if not s.eval_log_enabled or not answer.strip():
            return
        row = {"id": uuid.uuid4().hex[:12], "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "kind": kind,
               "question": question, "answer": answer, "contexts": [c for c in contexts if c], "reference": reference,
               "meta": meta or {}, "schema": SCHEMA}
        with _lock, open(s.eval_log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001
        log.exception("Could not write the evaluation log")


def read_log() -> list[dict]:
    path = get_settings().eval_log_path
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue  # a half-written line must not stop an evaluation
    return rows


def data_fingerprint() -> str:
    """A cheap fingerprint of the employee data (counts and sums). If it differs from the one logged with an answer, the data
    changed after the answer was given, and the deterministic checks do not compare that answer with the new data."""
    r = db.one("SELECT (SELECT COUNT(*) FROM employees) AS e, (SELECT COUNT(*) FROM employee_skills) AS s, "
               "(SELECT COALESCE(SUM(rating),0) FROM employee_skills) AS r, "
               "(SELECT COALESCE(SUM(LENGTH(canonical_skill)),0) FROM employee_skills) AS c")
    return f"{r['e']}-{r['s']}-{r['r']}-{r['c']}"


def split_by_schema(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """(current-format rows, older rows)."""
    new = [r for r in rows if int(r.get("schema") or 1) >= SCHEMA]
    return new, [r for r in rows if int(r.get("schema") or 1) < SCHEMA]


def archive_old_rows() -> tuple[int, str | None]:
    """Move rows in the old format out of interactions.jsonl into a dated archive file next to it. -> (rows moved, archive path)"""
    path = get_settings().eval_log_path
    new, old = split_by_schema(read_log())
    if not old:
        return 0, None
    archive = path.with_name(f"interactions-before-v{SCHEMA}-{datetime.now():%Y%m%d-%H%M%S}.jsonl")
    with _lock:
        archive.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in old), encoding="utf-8")
        path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in new), encoding="utf-8")
    return len(old), str(archive)
