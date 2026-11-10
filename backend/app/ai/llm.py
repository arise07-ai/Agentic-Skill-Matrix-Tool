"""LLM access (GPT-4.x / mini class via an OpenAI-compatible API, through LangChain).

Secrets: OPENAI_API_KEY (and optionally OPENAI_BASE_URL for Azure / a self-hosted fine-tune) come ONLY from the
environment. Without a key every function raises `AINotConfigured` and callers use their heuristic fallback.

"""
import json
import re

from ..config import get_settings
from . import AINotConfigured, cache
from .tracing import traced

_model = None
_error: str | None = None


def is_configured() -> bool:
    return bool(get_settings().openai_api_key)


def llm_error() -> str | None:
    return _error


def get_chat_model():
    """CUSTOMIZE: return any LangChain chat model (AzureChatOpenAI, ChatAnthropic, a local vLLM endpoint, ...)."""
    global _model, _error
    s = get_settings()
    if not s.openai_api_key:
        raise AINotConfigured("OPENAI_API_KEY is not set")
    if _model is None:
        try:
            from langchain_openai import ChatOpenAI  # type: ignore

            _model = ChatOpenAI(
                model=s.llm_model,             # Recommended: 'gpt-4o' or 'gpt-4o-mini'
                api_key=s.openai_api_key,
                base_url=s.openai_base_url or None,

                # --- CRITICAL FAITHFULNESS CONFIGURATION ---
                temperature=0.0,               # Completely removes creative freedom and variance.
                max_tokens=500,                # Tight cap keeps replies concise; long prose lowers scores.
                timeout=60,
                max_retries=2,

                # --- ADVANCED OPENAI STABILITY SETTINGS (explicit parameters, so LangChain does not warn about model_kwargs) ---
                seed=42,                       # Enforces deterministic responses across identical runs.
                top_p=0.00000001,              # Restricts token selection purely to the highest probability.
                presence_penalty=0.0,          # Stops the model from wandering to new topics.
                frequency_penalty=0.0,         # Keeps standard corporate lexicon stable.
                # -------------------------------------------
            )
        except ImportError as exc:
            _error = str(exc)
            raise AINotConfigured(f"langchain-openai is not installed ({exc})") from exc
    return _model



@traced("llm.complete")
def complete(system: str, user: str, *, use_cache: bool = True, namespace: str = "llm", max_tokens: int | None = None) -> str:
    """Return the model's text answer. Cached in Redis (exact) and, for opted-in namespaces, GPTCache (semantic).
    max_tokens raises (or lowers) the reply limit for this one call; without it the model's own cap (500) applies, which is right for
    short answers but cuts long output such as a study plan."""
    model = get_chat_model()
    if max_tokens:
        model = model.bind(max_tokens=max_tokens)
    key = json.dumps([get_settings().llm_model, system, user] + ([max_tokens] if max_tokens else []))
    if use_cache:
        hit = cache.cache_get(namespace, key)
        if isinstance(hit, str):
            return hit
    out = model.invoke([("system", system), ("human", user)]).content
    text = out if isinstance(out, str) else str(out)
    if use_cache:
        cache.cache_set(namespace, key, text)
    return text


def complete_json(system: str, user: str, *, namespace: str = "llm_json", max_tokens: int | None = None) -> dict:
    """Ask for JSON and parse it (tolerates ```json fences). max_tokens: see complete()."""
    text = complete(system, user, namespace=namespace, max_tokens=max_tokens).strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        return json.loads(text)
    except ValueError as exc:
        raise ValueError(f"The model did not return valid JSON: {text[:200]}") from exc