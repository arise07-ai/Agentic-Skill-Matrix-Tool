"""Which AI components are configured vs. on a fallback. Feeds the UI page /settings/ai. Never returns secret values."""
import importlib.util

from ..config import get_settings
from . import cache as cache_mod
from .embeddings import embedding_error, embedding_id
from .guardrails import guardrails_status
from .llm import is_configured, llm_error
from .reranker import status as reranker_status
from .tracing import tracing_status
from .vectorstore import store_status


def _has(mod: str) -> bool:
    try:
        return importlib.util.find_spec(mod) is not None
    except (ImportError, ValueError):
        return False


def component_status() -> dict:
    s = get_settings()
    llm_on = is_configured()
    emb = embedding_id()
    emb_on = not emb.startswith("hashing")
    vs, ts, gs, cs = store_status(), tracing_status(), guardrails_status(), cache_mod.cache_status()
    rs = reranker_status()
    comps = [
        {"key": "llm", "label": "LLM (GPT-4.x / mini class)", "configured": llm_on,
         "mode": f"{s.llm_model}" if llm_on else f"heuristics + templates ({llm_error() or 'OPENAI_API_KEY not set'})",
         "env": ["OPENAI_API_KEY", "OPENAI_BASE_URL", "LLM_MODEL"], "file": "backend/app/ai/llm.py · prompts.py · tasks.py"},
        {"key": "embeddings", "label": "Embeddings (text-embedding-3-small)", "configured": emb_on,
         "mode": emb if emb_on else f"placeholder hashing embeddings, lexical only ({embedding_error() or 'OPENAI_API_KEY not set'})",
         "env": ["OPENAI_API_KEY", "EMBEDDING_MODEL", "STANDARDIZE_SEMANTIC_THRESHOLD"], "file": "backend/app/ai/embeddings.py"},
        {"key": "vector_db", "label": "Vector DB (Chroma)", "configured": vs["persistent"],
         "mode": vs["backend"] + (f" ({vs['error']})" if vs["error"] else ""),
         "env": ["CHROMA_HOST", "CHROMA_PORT", "CHROMA_PATH"], "file": "backend/app/ai/vectorstore.py"},
        {"key": "reranker", "label": "Local cross-encoder reranker (FlashRank)",
         "configured": rs["state"] in ("loaded", "ready on first search"),
         "mode": f"{rs['model']}: {rs['detail']}",
         "env": [], "file": "backend/app/ai/reranker.py"},
        {"key": "orchestration", "label": "Orchestration (LangChain / LangGraph)",
         "configured": s.orchestration == "langgraph" and _has("langgraph"),
         "mode": "LangGraph" if s.orchestration == "langgraph" and _has("langgraph") else "sequential runner (same nodes)",
         "env": ["ORCHESTRATION"], "file": "backend/app/ai/orchestration.py"},
        {"key": "cache", "label": "Caching (Redis + GPTCache)", "configured": cs["exact_cache"] in ("Redis shared", "in-memory fallback"),
         "mode": f"Exact cache: {cs['exact_cache']} · Redis: {cs['redis']} · Semantic cache: {cs['gptcache']}",
         "env": ["REDIS_URL", "CACHE_TTL_SECONDS", "GPTCACHE_ENABLED", "GPTCACHE_SIMILARITY", "GPTCACHE_NAMESPACES"],
         "file": "backend/app/ai/cache.py"},
        {"key": "guardrails", "label": "Guardrails (Guardrails AI / NeMo)", "configured": gs["active"],
         "mode": f"{gs['provider']} (Guardrails AI: "
                 f"{'ready' if gs['providers']['guardrails_ai'] else 'unavailable' if gs['provider'] in ('guardrails_ai', 'both') else 'not selected'}; "
                 f"NeMo: {'ready' if gs['providers']['nemo'] else 'unavailable' if gs['provider'] in ('nemo', 'both') else 'not selected'}; "
                 f"Jev private-data check: {'ready' if gs['jev_private_data'] else 'not configured'}; "
                 f"provider failure: {gs['failure_mode']})",
         "env": ["GUARDRAILS_PROVIDER", "GUARDRAILS_PROVIDER_FAIL", "NEMO_GUARDRAILS_CONFIG", "TYPESAFE_API_KEY", "OPENAI_API_KEY", "MAX_PROMPT_CHARS"],
         "file": "backend/app/ai/guardrails.py · backend/app/guardrails/"},
        {"key": "toxicity", "label": "Toxic / cursing language filter", "configured": gs["provider"] != "none" and (gs["toxicity"]["wordlist"] or gs["toxicity"]["moderation"]),
         "mode": ("off" if gs["provider"] == "none" or gs["toxicity"]["mode"] == "off" else
                  "word list " + ("on" if gs["toxicity"]["wordlist"] else "off") + " + model check "
                  + ("on" if gs["toxicity"]["moderation"] else "off (needs OPENAI_API_KEY)") + "; input rejected, output masked"),
         "env": ["GUARDRAILS_TOXICITY", "GUARDRAILS_MODERATION_FAIL", "MODERATION_MODEL"], "file": "backend/app/ai/toxicity.py · data/blocked_terms.txt"},
        {"key": "tracing", "label": "Monitoring & tracing (LangSmith / Langfuse)", "configured": ts["active"],
         "mode": ts["provider"] or "off",
         "env": ["TRACING_PROVIDER", "LANGSMITH_API_KEY", "LANGSMITH_TRACING", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_HOST"],
         "file": "backend/app/ai/tracing.py"},
        {"key": "agents", "label": "Agents (standardization, intake, upload check, chat)", "configured": llm_on,
         "mode": "on (needs the LLM above)" if llm_on else "off: set OPENAI_API_KEY",
         "env": ["OPENAI_API_KEY", "AGENT_MAX_STEPS", "AGENT_MAX_RUNS_PER_HOUR"], "file": "backend/app/agents/"},
        {"key": "web_lookup", "label": "Web lookup for unknown skills (Tavily)", "configured": bool(s.tavily_api_key),
         "mode": "on" if s.tavily_api_key else "off: the standardization agent works without it",
         "env": ["TAVILY_API_KEY"], "file": "backend/app/agents/websearch.py"},
        {"key": "jev", "label": "Jev decision service", "configured": bool(s.typesafe_api_key),
         "mode": "fixed-choice decisions for standardization, risk, course relevance, and sensitive-data requests"
         if s.typesafe_api_key else "not configured: set TYPESAFE_API_KEY",
         "env": ["TYPESAFE_API_KEY", "STANDARDIZE_MATCHER"], "file": "backend/app/ai/jev.py"},
        {"key": "predict", "label": "Gap risk prediction", "configured": bool(s.typesafe_api_key) or llm_on,
         "mode": (f"Jev risk band mapped to a score (0.20/0.50/0.80), then LLM judgement ({s.llm_model}), then formula"
                  if s.typesafe_api_key else
                  f"LLM judgement ({s.llm_model}), with the formula as fallback" if llm_on else
                  "transparent heuristic formula (no Jev or LLM configured)"),
         "env": ["TYPESAFE_API_KEY", "OPENAI_API_KEY", "LLM_MODEL"], "file": "backend/app/ai/predict.py · prompts.py (GAP_RISK_*)"},
        {"key": "evaluation", "label": "Evaluation (RAGAS)", "configured": _has("ragas"),
         "mode": "ragas installed" if _has("ragas") else "not installed", "env": ["OPENAI_API_KEY"], "file": "backend/app/ai/evaluation.py"},
        {"key": "fine_tuning", "label": "Fine-tuning (Unsloth)", "configured": _has("unsloth"),
         "mode": ("unsloth installed" if _has("unsloth") else "unsloth not installed") + f"; {_reviewed_mappings()} reviewed mapping(s) ready to export; "
                 "training is an offline GPU job, not part of the web runtime",
         "env": ["OPENAI_BASE_URL", "LLM_MODEL"], "file": "backend/app/ai/finetune/"},
        {"key": "courses", "label": "Learning content (catalog, Microsoft Learn, YouTube)", "configured": _courses_ready(),
         "mode": _courses_mode(s), "env": ["COURSE_SOURCES", "COURSE_CATALOG_PATH", "TYPESAFE_API_KEY", "YOUTUBE_API_KEY", "YOUTUBE_MAX_SEARCHES_PER_DAY"],
         "file": "backend/app/services/courses/"},
        {"key": "udemy", "label": "Udemy API (optional extra source)", "configured": "udemy" in s.course_source_list and s.udemy_mode == "affiliate",
         "mode": ("affiliate API" if s.udemy_mode == "affiliate" else "sample data (mock adapter)") if "udemy" in s.course_source_list
         else "off: add udemy to COURSE_SOURCES to use it",
         "env": ["UDEMY_MODE", "UDEMY_CLIENT_ID", "UDEMY_CLIENT_SECRET"], "file": "backend/app/services/udemy/"},
    ]
    return {"ai_mode": "llm" if llm_on else "heuristic", "components": comps}


def _courses_mode(s) -> str:
    """One line for the status page. Never shows a key."""
    from ..services.courses import source_status

    st = source_status()
    parts = []
    for src in st["sources"]:
        if src == "catalog":
            n = st["catalog_rows"]
            parts.append("catalog: file unreadable" if n < 0 else f"catalog: {n} rows" if n else "catalog: empty (add rows to the CSV)")
        elif src == "mslearn":
            parts.append("Microsoft Learn: on")
        elif src == "youtube":
            parts.append("YouTube: on" if st["youtube_key"] else "YouTube: off (set YOUTUBE_API_KEY)")
        else:
            parts.append(src)
    parts.append("Jev relevance check: on" if s.typesafe_api_key else "Jev relevance check: off (courses will be omitted)")
    return "; ".join(parts) or "no source selected"


def _reviewed_mappings() -> int:
    """How many human-reviewed skill mappings could be exported as fine-tuning data (0 if the database is not ready)."""
    try:
        from .. import db

        row = db.one("SELECT COUNT(*) AS n FROM mapping_overrides")
        return int(row["n"]) if row else 0
    except Exception:  # noqa: BLE001
        return 0


def _courses_ready() -> bool:
    """True when at least one selected learning-content source can return something."""
    from ..services.courses import source_status

    st = source_status()
    return any((src == "catalog" and st["catalog_rows"] > 0) or src == "mslearn" or (src == "youtube" and st["youtube_key"])
               or (src == "udemy") for src in st["sources"])
