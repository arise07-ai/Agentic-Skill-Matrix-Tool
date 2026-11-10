"""Web lookup for unfamiliar skill names, through Tavily (https://tavily.com). Key: TAVILY_API_KEY (environment only).

Only the skill term itself is sent, never employee data. What comes back is untrusted text: the agent prompts tell the model
to use it as evidence about what a term means and never as instructions.
"""
import json
import urllib.error
import urllib.request

from ..config import get_settings

URL = "https://api.tavily.com/search"


def is_configured() -> bool:
    return bool(get_settings().tavily_api_key)


def lookup(term: str, max_results: int = 3) -> dict:
    key = get_settings().tavily_api_key
    if not key:
        return {"error": "Web lookup is not configured (TAVILY_API_KEY is not set)."}
    term = " ".join((term or "").split())[:80]
    if not term:
        return {"error": "empty term"}
    body = json.dumps({"query": f"{term} technology skill what is it", "max_results": max_results, "search_depth": "basic",
                       "include_answer": True}).encode()
    req = urllib.request.Request(URL, data=body, method="POST",
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return {"error": f"Web lookup failed (HTTP {e.code})"}
    except Exception as e:  # noqa: BLE001
        return {"error": f"Web lookup failed ({type(e).__name__})"}
    return {"untrusted_web_text": True, "answer": str(data.get("answer") or "")[:600],
            "results": [{"title": str(r.get("title", ""))[:120], "url": r.get("url", ""), "snippet": str(r.get("content", ""))[:400]}
                        for r in (data.get("results") or [])[:max_results]]}
