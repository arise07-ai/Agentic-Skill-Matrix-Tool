"""Small JSON-file cache for course lookups (Microsoft Learn catalog, YouTube results, YouTube daily counter)."""
import json
import logging
import time
from datetime import date

from ...config import get_settings

log = logging.getLogger(__name__)


def _path(name: str):
    return get_settings().cache_dir / name


def read(name: str, max_age_seconds: float | None = None, allow_stale: bool = False):
    """The cached value, or None when missing, unreadable or older than max_age_seconds (unless allow_stale)."""
    try:
        raw = json.loads(_path(name).read_text(encoding="utf-8"))
        if not allow_stale and max_age_seconds is not None and time.time() - raw["at"] > max_age_seconds:
            return None
        return raw["value"]
    except (OSError, ValueError, KeyError, TypeError):
        return None


def write(name: str, value) -> None:
    try:
        p = _path(name)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps({"at": time.time(), "value": value}, ensure_ascii=False), encoding="utf-8")
        tmp.replace(p)
    except (OSError, TypeError, ValueError):
        log.warning("Could not write cache file %s", name, exc_info=True)


def count_today(name: str) -> int:
    v = read(name)
    return int(v["n"]) if isinstance(v, dict) and v.get("day") == date.today().isoformat() else 0


def bump_today(name: str) -> int:
    n = count_today(name) + 1
    write(name, {"day": date.today().isoformat(), "n": n})
    return n
