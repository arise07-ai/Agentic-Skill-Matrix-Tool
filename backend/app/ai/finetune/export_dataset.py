"""Export human-reviewed skill mappings as a JSONL fine-tuning dataset (chat format).

Run (from backend/):  python -m app.ai.finetune.export_dataset > skill_norm.jsonl

Each reviewed mapping (the manual corrections made on the Standardization page) becomes one
training example for the skill-standardization task. Review the output for sensitive data
before using it to train.
"""
import json
import sys

from ... import db
from .. import prompts


def main() -> None:
    db.init_db()
    for r in db.rows("SELECT raw_key, canonical FROM mapping_overrides"):
        ex = {
            "messages": [
                {"role": "system", "content": prompts.STANDARDIZE_SYSTEM},
                {"role": "user", "content": prompts.STANDARDIZE_USER.format(raw=r["raw_key"], candidates="[...]")},
                {"role": "assistant", "content": json.dumps({"canonical": r["canonical"]})},
            ]
        }
        sys.stdout.write(json.dumps(ex) + "\n")


if __name__ == "__main__":
    main()
