"""Broad skills (Cloud, Cybersecurity, Mobile Development…) made of specific tools (AWS, Terraform, Splunk…).

Projects ask for broad skills; people are rated on specific tools. Without a link, "Cloud" is never met by someone with AWS 5 and
Terraform 5. data/skill_groups.json lists, per broad skill, the tools that count towards it. A person's rating for the broad skill
is the BEST rating among those tools (or their own rating of the broad skill itself, if higher). Tools match by whole words from
the start of the name, so "AWS" matches "AWS (Well-Architected)" and "AWS Lambda", but "Java" does not match "JavaScript".
"""
import json
import re
from functools import lru_cache
from pathlib import Path

FILE = Path(__file__).resolve().parent.parent / "data" / "skill_groups.json"


def _tokens(name: str) -> tuple[str, ...]:
    return tuple(re.findall(r"[a-z0-9+#]+", name.lower().replace("&", " and ")))


def _group_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", name.casefold()).strip()


@lru_cache(maxsize=1)
def load() -> dict[str, list[tuple[str, tuple[str, ...]]]]:
    """{broad skill: [(tool name, its word tokens)]}. A missing or broken file means no groups, never a crash."""
    try:
        raw = json.loads(FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {g: [(t, _tokens(t)) for t in tools if _tokens(t)] for g, tools in raw.items()
            if not g.startswith("_") and isinstance(tools, list)}


@lru_cache(maxsize=1)
def aliases() -> dict[str, str]:
    """{alternate group name: canonical group name}."""
    try:
        raw = json.loads(FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    configured = raw.get("_aliases", {})
    if not isinstance(configured, dict):
        return {}
    canonical_names = {name.lower(): name for name in load()}
    return {
        alias.lower(): canonical_names[target.lower()]
        for alias, target in configured.items()
        if isinstance(alias, str)
        and isinstance(target, str)
        and alias.strip()
        and target.lower() in canonical_names
    }


@lru_cache(maxsize=1)
def related_skills() -> dict[str, list[str]]:
    """{target skill: explicitly related skills used to identify upskilling candidates}."""
    try:
        raw = json.loads(FILE.read_text(encoding="utf-8")).get("_related_skills", {})
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {
        str(target): [str(skill) for skill in skills if isinstance(skill, str) and skill.strip()]
        for target, skills in raw.items()
        if isinstance(target, str) and target.strip() and isinstance(skills, list)
    }


def related_skills_of(skill: str) -> list[str]:
    key = re.sub(r"[^a-z0-9]+", " ", skill.casefold()).strip()
    return next(
        (skills for target, skills in related_skills().items()
         if re.sub(r"[^a-z0-9]+", " ", target.casefold()).strip() == key),
        [],
    )


def reload() -> None:
    load.cache_clear()
    aliases.cache_clear()
    related_skills.cache_clear()


def names() -> list[str]:
    return list(load())


def is_group(skill: str) -> bool:
    return match(skill) is not None


def match(name: str) -> str | None:
    """The canonical broad skill for a group name or configured alias."""
    key = _group_key(name or "")
    canonical = next((g for g in load() if _group_key(g) == key), None)
    return canonical or next((target for alias, target in aliases().items() if _group_key(alias) == key), None)


def _is_member(skill_name: str, member: tuple[str, ...]) -> bool:
    tk = _tokens(skill_name)
    return len(tk) >= len(member) and tk[: len(member)] == member


def effective(ratings: dict[str, int], skill: str) -> tuple[int, str | None]:
    """(rating, via). via is the tool that gave a broad skill its rating, or None when it is the person's own rating."""
    canonical = match(skill) or skill
    canonical_key = _group_key(canonical)
    alias_names = {
        _group_key(alias) for alias, target in aliases().items() if _group_key(target) == canonical_key
    }
    own = max(
        dict.get(ratings, canonical) or 0,
        dict.get(ratings, skill) or 0,
        max((value or 0 for alias, value in ratings.items() if _group_key(alias) in alias_names), default=0),
    )
    best, via = own, None
    for _, member in load().get(canonical, []):
        for sk, val in ratings.items():
            if (
                sk != canonical
                and _group_key(sk) not in alias_names
                and sk != skill
                and (val or 0) > best
                and _is_member(sk, member)
            ):
                best, via = val or 0, sk
    return best, via


def tools_of(skill: str) -> list[str]:
    return [t for t, _ in load().get(skill, [])]


class Ratings(dict):
    """{skill: rating} whose .get(skill) already counts the tools of a broad skill. For checks and plans."""
    def get(self, skill, default=0):  # noqa: D401
        return effective(self, skill)[0] if (skill in self or is_group(skill)) else default
