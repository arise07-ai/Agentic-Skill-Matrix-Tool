"""Show, step by step, how one raw skill is standardized. Read-only: nothing is saved.

    python -m app.ai.std_trace "Spark job tuning" ["dashboard building in PowerBI" ...]

Use it to see WHY a case ends as "new": was it missing from the embedding top 5, below the model gate, never offered to the model, or did the model say no?
"""
import sys

from .. import db
from ..config import get_settings
from ..services import standardization as st
from . import tasks, vectorstore


def trace(raw: str) -> None:
    s = get_settings()
    db.init_db()
    tax, lookup, loose = st._load()
    raw = " ".join(raw.split())
    print(f"\nRaw skill: {raw!r}")
    ov = db.one("SELECT canonical FROM mapping_overrides WHERE raw_key=?", (raw.lower(),))
    print("1. manual override :", ov["canonical"] if ov else "none")
    print("2. exact name/alias:", lookup.get(raw.lower()) or loose.get(st._np(raw)) or "none")
    try:
        top = [(i, sim) for i, sim, _ in vectorstore.query(st.TAX_COLLECTION, raw, 5)]
    except Exception as e:  # noqa: BLE001
        top = []
        print("3. embedding top 5 : unavailable:", e)
    else:
        print("3. embedding top 5 :", ", ".join(f"{n} ({sim:.2f})" for n, sim in top) or "none")
    gate = s.standardize_model_min_similarity
    named = st.skills_named_in(raw, lookup)
    print(f"4. skills named in the phrase: {named or 'none'}")
    best = top[0][1] if top else 0.0
    asked = bool(named) or best >= gate
    print(f"5. model asked?    : {'yes' if asked else 'NO'}  (gate {gate:.2f}, best match {best:.2f}, named skill {'yes' if named else 'no'})")
    if asked:
        cands = list(dict.fromkeys([*named[:3], *[n for n, _ in top]]))[:7]
        print("   candidates given:", cands)
        try:
            print("   model's answer   :", tasks.match_skills(raw, cands))
        except Exception as e:  # noqa: BLE001
            print("   model call failed:", type(e).__name__, e)
    print("Final result       :", st.normalize(raw))


if __name__ == "__main__":
    for r in sys.argv[1:] or ["Spark job tuning"]:
        trace(r)
