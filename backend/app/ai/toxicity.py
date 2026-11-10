"""TOXIC / CURSING LANGUAGE guardrail, two layers.

  1. Word list (offline, no key): data/blocked_terms.txt, whole words, disguises like f*ck / sh1t / fuuuck are caught.
  2. Model check (needs OPENAI_API_KEY): OpenAI's moderation endpoint, which also catches harassment, hate and threats that
     contain no listed word. The word list runs first; the model is only asked when the list found nothing.

GUARDRAILS_TOXICITY = off | wordlist | moderation | both   (default both)
Input is REJECTED (the person is asked to rephrase). Output is MASKED: a listed word becomes f***; if only the model flags the
answer, the whole answer is replaced by a neutral message, because there is no single word to mask.
If the moderation service cannot be reached, GUARDRAILS_MODERATION_FAIL decides: open = let the text through (default),
closed = reject it / withhold the answer.
"""
import json
import logging
import re
import urllib.error
import urllib.request
from functools import lru_cache
from pathlib import Path

from ..config import get_settings

log = logging.getLogger(__name__)
TERMS_FILE = Path(__file__).resolve().parent.parent / "data" / "blocked_terms.txt"
# moderation categories that count as toxic here (violence/sexual in general are not: HR text can mention incidents)
TOXIC_CATEGORIES = ("harassment", "harassment/threatening", "hate", "hate/threatening", "sexual/minors")
WITHHELD = "This answer was withheld because it may contain inappropriate language. Please rephrase your question."

_LOOK = {"a": "a@4", "e": "e3", "i": "i1!", "o": "o0", "s": "s$5", "t": "t7", "l": "l1", "b": "b8"}


def _word_pattern(term: str) -> str:
    """'shit' -> letters may be repeated, disguised (sh1t, $hit), starred out (sh*t) or dotted (s.h.i.t)."""
    parts = []
    for word in term.split():
        letters = []
        for ch in word.lower():
            cls = re.escape(_LOOK.get(ch, ch)) if len(_LOOK.get(ch, ch)) == 1 else "[" + re.escape(_LOOK[ch]) + "]"
            letters.append(f"(?:{cls}|[*#])+")
        parts.append(r"[.\-_*]?".join(letters))
    return r"\s+".join(parts)


@lru_cache(maxsize=1)
def _compiled() -> re.Pattern | None:
    try:
        terms = [ln.strip() for ln in TERMS_FILE.read_text(encoding="utf-8").splitlines() if ln.strip() and not ln.startswith("#")]
    except OSError:
        return None
    if not terms:
        return None
    terms.sort(key=len, reverse=True)  # longer terms first: 'motherfucker' before 'fucker'
    return re.compile(r"(?<![A-Za-z0-9])(?:" + "|".join(_word_pattern(t) for t in terms) + r")(?![A-Za-z0-9])", re.I)


def reload() -> None:
    _compiled.cache_clear()


def mode() -> str:
    m = (get_settings().guardrails_toxicity or "both").lower()
    return m if m in ("off", "wordlist", "moderation", "both") else "both"


def find_terms(text: str) -> list[str]:
    pat = _compiled()
    return [m.group(0) for m in pat.finditer(text)] if pat and mode() in ("wordlist", "both") else []


def mask(text: str) -> tuple[str, int]:
    """Replace every listed word by its first letter plus asterisks. -> (text, how many)"""
    pat = _compiled()
    if not pat or mode() not in ("wordlist", "both"):
        return text, 0
    return pat.subn(lambda m: m.group(0)[0] + "*" * (len(m.group(0)) - 1), text)


def moderate(text: str) -> tuple[bool | None, list[str]]:
    """Ask the moderation endpoint. -> (flagged, categories). flagged is None when the service could not be used."""
    s = get_settings()
    if mode() not in ("moderation", "both") or not s.openai_api_key:
        return False, []
    url = (s.openai_base_url or "https://api.openai.com/v1").rstrip("/") + "/moderations"
    req = urllib.request.Request(url, data=json.dumps({"model": s.moderation_model, "input": text[:20000]}).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {s.openai_api_key}"})
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            res = json.loads(resp.read().decode("utf-8"))["results"][0]
    except Exception as e:  # noqa: BLE001  never include the key or response in logs
        log.warning("Moderation check unavailable (%s); GUARDRAILS_MODERATION_FAIL=%s", type(e).__name__, s.guardrails_moderation_fail)
        return None, []
    cats = [c for c in TOXIC_CATEGORIES if (res.get("categories") or {}).get(c)]
    return bool(cats), cats


def fail_closed() -> bool:
    return get_settings().guardrails_moderation_fail.lower() == "closed"


def status() -> dict:
    s = get_settings()
    m = mode()
    return {"mode": m, "wordlist": m in ("wordlist", "both") and _compiled() is not None,
            "moderation": m in ("moderation", "both") and bool(s.openai_api_key), "fail": s.guardrails_moderation_fail}
