"""Adds the sensitive-employee-data guardrail to YOUR files in place.  Run from the backend folder:   python apply_privacy_fix.py
Edits app/ai/guardrails.py and app/agents/chat.py. Each replaced text must appear exactly once, otherwise NOTHING is written.
Backups: <file>.bak"""
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent / "app"

GUARD_NEW_CODE = r'''class PrivacyViolation(GuardrailViolation):
    """A question that asks for sensitive personal data the skill matrix must never give out (salary, home address, health...)."""


_PRIVATE = (
    r"salary|salaries|wages?|pay ?slips?|pay ?checks?|take-home pay|pay ?rate|pay ?grade|compensation package|bonus(?:es)?|"
    r"home address|residential address|home phone|phone numbers?|mobile numbers?|cell(?:phone)? numbers?|personal (?:e-?mail|phone|contact)\w*|"
    r"date of birth|birth ?dates?|birthdays?|\bssn\b|social security|national id|passport|tax id|driver'?s licen[sc]e|"
    r"bank (?:accounts?|details)|\biban\b|credit card|passwords?|passcodes?|"
    r"marital status|spouse|religio\w*|sexual orientation|ethnicity|"
    r"medical (?:records?|history|conditions?|leave)|health (?:conditions?|records?|issues?|status|information)|diagnos\w*|disabilit\w*|pregnan\w*|"
    r"disciplinary|criminal records?|immigration status|visa status"
)
_PRIVATE_RE = re.compile(r"(?:" + _PRIVATE + r")", re.I)
# "salary negotiation skills", "passport control training": the word names a SKILL or course, not a person's data
_SKILL_USE_RE = re.compile(r"(?:" + _PRIVATE + r")(?:\s+\w+){0,2}\s+(?:skills?|experience|expertise|training|courses?|certifications?|knowledge|"
                           r"negotiation|benchmarking|analysis|analytics|administration|management)\b", re.I)
# the question asks for it (a request word, a possessive, "of/for someone", "everyone's ...") ...
_ASKING_RE = re.compile(r"\b(?:what|what's|whats|how much|how old|show|tell|give|list|share|reveal|display|find|get|look ?up|provide|send|export|"
                        r"rank|compare|sort|order|average|highest|lowest|top|biggest|largest|smallest|of|for|their|his|her|everyone|anyone|anybody|someone|"
                        r"any|is|are|was|does|do|has|have|employees?|staff|people|colleagues?)\b|['’]s\b", re.I)
# ... or asks who earns what without naming the word
_EARNINGS_RE = re.compile(r"\b(?:earns?|earning|highest[- ]paid|best[- ]paid|lowest[- ]paid|well[- ]paid|underpaid|overpaid|get paid|is paid|are paid)\b|"
                          r"\bhow much (?:does|do|did|is|are)\b.{1,40}\b(?:make|get|earn)\b", re.I)
PRIVACY_MESSAGE = ("I can't help with requests for sensitive personal or employee data, such as salary, home address, phone number, personal "
                   "email, date of birth, ID numbers, bank details, health, family or disciplinary information. The skill matrix only holds "
                   "skills, roles and learning information, so please ask about those instead.")


def _reject_private_data_request(text: str) -> None:
    """Chat questions only (check_input(..., privacy_check=True)). Built-in and rule-based: it blocks a clear request for a person's
    sensitive data before any answer is written. It is a safety net on top of the fact that the matrix holds no such data."""
    if _EARNINGS_RE.search(text):
        raise PrivacyViolation(PRIVACY_MESSAGE)
    rest = _SKILL_USE_RE.sub(" ", text)
    if _PRIVATE_RE.search(rest) and _ASKING_RE.search(rest):
        raise PrivacyViolation(PRIVACY_MESSAGE)


'''

EDITS = {
    "ai/guardrails.py": [
        ('''def _guardrails_ai_check(text: str) -> None:''', GUARD_NEW_CODE + '''def _guardrails_ai_check(text: str) -> None:'''),
        ('''def check_input(text: str, toxicity_check: bool = True) -> str:
    """Validate user-supplied text going into a prompt. Empty text is allowed (returns '').
    toxicity_check=False is for bulk file text (uploaded employee files), which is not rejected over a single word."""''',
         '''def check_input(text: str, toxicity_check: bool = True, privacy_check: bool = False) -> str:
    """Validate user-supplied text going into a prompt. Empty text is allowed (returns '').
    toxicity_check=False is for bulk file text (uploaded employee files), which is not rejected over a single word.
    privacy_check=True (the chat) also refuses requests for a person's sensitive data (salary, address, health...)."""'''),
        ('''    if toxicity_check:
        _reject_toxic_input(text)
    extra =''', '''    if privacy_check:
        _reject_private_data_request(text)
    if toxicity_check:
        _reject_toxic_input(text)
    extra ='''),
    ],
    "agents/chat.py": [
        ('''        check_input(history[-1]["content"])
    except ToxicInputViolation as e:''', '''        check_input(history[-1]["content"], privacy_check=True)
    except ToxicInputViolation as e:'''),
    ],
}

new_text = {}
for rel, pairs in EDITS.items():
    path = ROOT / rel
    if not path.exists():
        sys.exit(f"STOP: {path} not found. Run this from the backend folder (the one that contains app/).")
    s = path.read_text(encoding="utf-8")
    if "privacy_check" in s:
        sys.exit(f"STOP: {rel} already has this fix. Nothing changed.")
    for old, new in pairs:
        if s.count(old) != 1:
            sys.exit(f"STOP: in {rel} I expected this text exactly once but found it {s.count(old)} time(s):\n---\n{old[:200]}\n---\n"
                     f"Nothing was changed. Send me your current {rel}.")
        s = s.replace(old, new)
    new_text[path] = s
for path, s in new_text.items():
    shutil.copy2(path, str(path) + ".bak")
    path.write_text(s, encoding="utf-8")
    print("updated", path.relative_to(ROOT.parent), "(backup: .bak)")
print("Done. Restart the backend.")
