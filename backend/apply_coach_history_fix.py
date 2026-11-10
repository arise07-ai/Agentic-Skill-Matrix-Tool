"""Fixes the Learning Coach repeating its 'please rephrase' reply after a polite message.  Run from the backend folder:
    python apply_coach_history_fix.py
Edits app/services/learning.py in place. The replaced text must appear exactly once, otherwise nothing is written. Backup: learning.py.bak"""
import shutil
import sys
from pathlib import Path

path = Path(__file__).resolve().parent / "app" / "services" / "learning.py"
OLD = '''    messages = [
        {"role": message["role"],
         "content": check_input(message["content"]) if message["role"] == "user" else message["content"]}
        for message in messages
    ]
'''
NEW = '''    # Only the NEW message must pass the guardrails (a refusal is shown to the person). Earlier turns were checked when they were asked: one that
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
'''
if not path.exists():
    sys.exit(f"STOP: {path} not found. Run this from the backend folder (the one that contains app/).")
s = path.read_text(encoding="utf-8")
if "left out of the conversation now" in s:
    sys.exit("STOP: this fix is already applied. Nothing changed.")
if s.count(OLD) != 1:
    sys.exit(f"STOP: expected text found {s.count(OLD)} time(s), not once. Nothing changed. Send me your current services/learning.py.")
shutil.copy2(path, str(path) + ".bak")
path.write_text(s.replace(OLD, NEW), encoding="utf-8")
print("updated app/services/learning.py (backup: learning.py.bak)")
