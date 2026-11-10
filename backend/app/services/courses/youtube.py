"""YouTube Data API v3: finds tutorial videos. Needs YOUTUBE_API_KEY (environment / secret store only).

Per lookup: one search.list call (YouTube allows about 100 a day by default) plus one videos.list call to learn the video lengths.
Results are cached per skill and level (YOUTUBE_CACHE_DAYS) and the number of searches per day is capped (YOUTUBE_MAX_SEARCHES_PER_DAY),
so the allowance is not burned. The key is sent in the `x-goog-api-key` header, never in the URL, and is masked in error messages.
"""
import html
import logging
import re

import httpx

from ...config import get_settings
from . import cache
from .. import groups
from .base import Course, SourceError, is_about

log = logging.getLogger(__name__)
SEARCH = "https://www.googleapis.com/youtube/v3/search"
VIDEOS = "https://www.googleapis.com/youtube/v3/videos"
_WORD = {"beginner": "for beginners", "intermediate": "intermediate", "expert": "advanced"}


def _iso_hours(d: str) -> float | None:
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", d or "")
    if not m:
        return None
    days, h, mi, sec = (int(x or 0) for x in m.groups())
    return round(days * 24 + h + mi / 60 + sec / 3600, 2)


def _get(url: str, params: dict, key: str) -> dict:
    try:
        r = httpx.get(url, params=params, headers={"x-goog-api-key": key}, timeout=15)
    except httpx.HTTPError as e:
        raise SourceError(f"YouTube could not be reached ({type(e).__name__}).") from e
    if r.status_code != 200:
        reason = ""
        try:
            reason = r.json()["error"]["errors"][0]["reason"]
        except Exception:  # noqa: BLE001
            pass
        if reason in ("quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded"):
            raise SourceError("YouTube's daily allowance is used up; videos are skipped until it resets.")
        if r.status_code in (400, 403) and reason in ("keyInvalid", "API_KEY_INVALID", "accessNotConfigured", "forbidden", ""):
            raise SourceError(f"YouTube refused the key (HTTP {r.status_code}). Check YOUTUBE_API_KEY and that YouTube Data API v3 is enabled for it.")
        raise SourceError(f"YouTube returned HTTP {r.status_code}.")
    return r.json()


def search(skill: str, level: str | None = None, limit: int = 2) -> list[Course]:
    s = get_settings()
    key = s.youtube_api_key
    if not key:
        return []
    cache_name = "yt_" + re.sub(r"[^a-z0-9]+", "-", f"{skill}-{level or 'any'}".lower()).strip("-")[:80] + ".json"
    hit = cache.read(cache_name, s.youtube_cache_days * 86400)
    if hit is None:
        if cache.count_today("yt_searches_today.json") >= s.youtube_max_searches_per_day:
            raise SourceError("YouTube search limit for today reached (YOUTUBE_MAX_SEARCHES_PER_DAY); videos are skipped until tomorrow.")
        cache.bump_today("yt_searches_today.json")
        q = f"{skill} tutorial {_WORD.get(level or '', '')}".strip()
        try:
            found = _get(SEARCH, {"part": "snippet", "type": "video", "q": q, "maxResults": 8, "videoEmbeddable": "true",
                                  "safeSearch": "strict", "relevanceLanguage": "en"}, key).get("items", [])
            ids = [i["id"]["videoId"] for i in found if i.get("id", {}).get("videoId")]
            length = {}
            if ids:
                for v in _get(VIDEOS, {"part": "contentDetails", "id": ",".join(ids)}, key).get("items", []):
                    length[v["id"]] = _iso_hours(v.get("contentDetails", {}).get("duration", ""))
        except SourceError:
            raise
        except (KeyError, ValueError, TypeError) as e:
            raise SourceError(f"YouTube answered in an unexpected format ({type(e).__name__}).") from e
        hit = [{"id": i["id"]["videoId"], "title": html.unescape(i.get("snippet", {}).get("title", "")),
                "description": html.unescape(i.get("snippet", {}).get("description", ""))[:1200],
                "channel": html.unescape(i.get("snippet", {}).get("channelTitle", "")), "hours": length.get(i["id"]["videoId"])}
               for i in found if i.get("id", {}).get("videoId")]
        cache.write(cache_name, hit)
    out = []
    tools = [n for n, _ in groups.load().get(groups.match(skill) or "", [])]  # a broad skill: a video about one of its tools counts
    hit = [v for v in hit if is_about(v.get("title", ""), skill, tools)]  # drop off-topic results (an accounting video for "Real-time Transactions")
    # relevance order from YouTube, but real tutorials (over about 9 minutes) before short clips
    for v in sorted(hit, key=lambda v: 0 if (v.get("hours") or 0) >= 0.15 else 1):
        if not v["title"]:
            continue
        out.append(Course(id=f"youtube-{v['id']}", title=v["title"] + (f" ({v['channel']})" if v.get("channel") else ""),
                          url=f"https://www.youtube.com/watch?v={v['id']}", level=level or "all", skill=skill,
                          duration_hours=v.get("hours"), provider="YouTube", kind="video",
                          description=v.get("description", "")[:1200]))
    return out[:limit]
