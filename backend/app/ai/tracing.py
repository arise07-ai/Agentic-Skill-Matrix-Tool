"""MONITORING & TRACING (Langfuse or LangSmith).

TRACING_PROVIDER = auto | none | langfuse | langsmith
  auto      -> Langfuse if LANGFUSE_PUBLIC_KEY/SECRET_KEY are set, else LangSmith if LANGSMITH_API_KEY is set, else off.
Keys come from the environment only. Add `@traced("name")` to any function to record it as a span.
"""
from typing import Callable

from ..config import get_settings


def _resolve():
    s = get_settings()
    p = s.tracing_provider.lower()
    if p == "none":
        return None
    order = [p] if p in {"langfuse", "langsmith"} else ["langfuse", "langsmith"]
    for name in order:
        if name == "langfuse" and s.langfuse_public_key and s.langfuse_secret_key:
            try:
                from langfuse import observe  # type: ignore  # SDK v3

                return name, observe
            except ImportError:
                try:
                    from langfuse.decorators import observe  # type: ignore  # SDK v2

                    return name, observe
                except ImportError:
                    continue
        if name == "langsmith" and s.langsmith_api_key:
            try:
                from langsmith import traceable  # type: ignore

                return name, traceable
            except ImportError:
                continue
    return None


def traced(name: str) -> Callable:
    def deco(fn: Callable) -> Callable:
        found = _resolve()
        if not found:
            return fn
        try:
            return found[1](name=name)(fn)
        except Exception:  # tracing must never break the app
            return fn

    return deco


def tracing_status() -> dict:
    found = _resolve()
    return {"provider": found[0] if found else None, "active": bool(found)}
