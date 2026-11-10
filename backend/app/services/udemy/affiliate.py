"""Udemy Affiliate API client (public course catalogue, HTTP Basic auth with client id/secret).

UNTESTED: written from the documented endpoint (GET /api-2.0/courses/?search=...). Field names
(`title`, `url`, `avg_rating`, `content_info`) and level filtering need to be verified against a
real response with your credentials. Credentials come ONLY from env vars.
"""
import httpx

from .base import Course

API = "https://www.udemy.com/api-2.0/courses/"


class AffiliateUdemyClient:
    def __init__(self, client_id: str, client_secret: str):
        if not client_id or not client_secret:
            raise RuntimeError("UDEMY_CLIENT_ID and UDEMY_CLIENT_SECRET must be set when UDEMY_MODE=affiliate")
        self._auth = (client_id, client_secret)

    def search(self, skill: str, level: str | None = None, limit: int = 3) -> list[Course]:
        params = {"search": skill, "page_size": limit * 3, "ordering": "relevance", "language": "en"}
        r = httpx.get(API, params=params, auth=self._auth, timeout=15)
        r.raise_for_status()
        out: list[Course] = []
        for c in r.json().get("results", []):
            lvl = (c.get("instructional_level") or "all").lower()
            lvl = "expert" if "expert" in lvl else "beginner" if "beginner" in lvl else "intermediate" if "intermediate" in lvl else "all"
            out.append(Course(id=str(c.get("id")), title=c.get("title", ""), url="https://www.udemy.com" + c.get("url", ""),
                              level=lvl, skill=skill, rating=c.get("avg_rating"),
                              duration_hours=None))  # TODO: parse c['content_info'] ("5.5 total hours")
        out.sort(key=lambda c: 0 if level and c.level in (level, "all") else 1)
        return out[:limit]
