"""Application settings. Every value comes from environment variables (or a git-ignored `.env`).

Secrets (OPENAI_API_KEY, LANGFUSE_*, LANGSMITH_API_KEY, UDEMY_CLIENT_SECRET, YOUTUBE_API_KEY, ...) are NEVER hardcoded:
set them in your shell, your platform's secret store, or `.env`. Real environment variables always win over `.env`.
"""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
ENV_FILES = (str(BACKEND_DIR.parent / ".env"), str(BACKEND_DIR / ".env"))

try:  # also export .env into os.environ so third-party SDKs (LangSmith, Langfuse, OpenAI) can see it
    from dotenv import load_dotenv

    for _f in ENV_FILES:
        load_dotenv(_f, override=False)
except ImportError:  # pragma: no cover
    pass


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILES, extra="ignore")

    # ---- storage
    data_dir: str = str(BACKEND_DIR / "data")
    chroma_path: str = ""  # default: <data_dir>/chroma
    chroma_host: str = ""
    chroma_port: int = 8000
    redis_url: str = ""
    cache_ttl_seconds: int = 86400

    # ---- LLM / embeddings (OpenAI-compatible). Key is read from the environment only.
    openai_api_key: str = ""
    openai_base_url: str = ""
    llm_model: str = "gpt-4.1 mini"  # any GPT-4.x / mini class model
    embedding_model: str = "text-embedding-3-small"

    # ---- Jev (TypeSafe AI): a decision model that picks an option from a fixed list. Key from the environment only (TYPESAFE_API_KEY).
    typesafe_api_key: str = ""
    standardize_model_min_similarity: float = 0.5  # a model is asked to pick a skill only if the best embedding match scores at least this
    standardize_matcher: str = "llm"  # who picks among the candidate skills: llm (default) or jev (falls back to llm if Jev fails)

    # ---- semantic cache (GPTCache) on top of Redis exact-match cache
    gptcache_enabled: bool = False
    gptcache_similarity: float = 0.95
    gptcache_namespaces: str = "requirements"  # comma separated

    # ---- orchestration / guardrails / tracing
    orchestration: str = "sequential"  # sequential | langgraph
    guardrails_provider: str = "builtin"  # none | builtin | guardrails_ai | nemo | both
    guardrails_provider_fail: str = "closed"  # open | closed; behavior when an external provider cannot run
    nemo_guardrails_config: str = str(BACKEND_DIR / "app" / "guardrails")
    max_prompt_chars: int = 4000
    guardrails_toxicity: str = "both"  # off | wordlist | moderation | both  (toxic / cursing language)
    guardrails_moderation_fail: str = "open"  # open = allow when the moderation service is unreachable | closed = block
    moderation_model: str = "omni-moderation-latest"
    tracing_provider: str = "auto"  # auto | none | langfuse | langsmith
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = ""
    langsmith_api_key: str = ""
    langsmith_tracing: bool = False
    langsmith_project: str = "skill-matrix"

    # ---- prompt style, one setting per prompt: few (default: worked examples are added) | zero (instructions only)
    prompt_style_standardize: str = "few"
    prompt_style_requirements: str = "few"
    prompt_style_gap_risk: str = "few"

    # ---- learning content: your own catalog (CSV) + Microsoft Learn (no key) + YouTube (needs YOUTUBE_API_KEY)
    course_sources: str = "catalog,mslearn,youtube"  # comma separated, in priority order; "udemy" may be added (see below)
    course_catalog_path: str = ""  # default: backend/app/data/course_catalog.csv
    course_cache_hours: int = 24  # how long the Microsoft Learn catalog is kept on disk
    mslearn_locale: str = "en-us"
    youtube_api_key: str = ""  # secret: environment only
    youtube_max_searches_per_day: int = 90  # YouTube's default allowance is about 100 searches a day
    youtube_cache_days: int = 7

    # ---- Udemy adapter (optional extra source; add "udemy" to COURSE_SOURCES)
    udemy_mode: str = "mock"  # mock | affiliate
    udemy_client_id: str = ""
    udemy_client_secret: str = ""

    # ---- app behaviour
    cors_origins: str = "http://localhost:3000"
    max_upload_mb: int = 10
    standardize_semantic_threshold: float = 0.82
    standardize_fuzzy_threshold: float = 90.0
    adjacency_min_similarity: float = 0.6
    staffing_weight_skill: float = 0.6
    staffing_weight_semantic: float = 0.3
    staffing_weight_role: float = 0.1

    # ---- login and roles (secrets come from the environment only)
    auth_secret: str = ""  # signs session cookies; if empty a random one is made at start-up (sessions end on restart)
    admin_username: str = ""  # the first admin account is created from these two on start-up
    admin_password: str = ""
    session_hours: int = 12
    auth_cookie_secure: bool = False  # set true when the app is served over HTTPS

    # ---- agents
    agent_max_steps: int = 14  # model/tool round trips per run
    agent_max_runs_per_hour: int = 30  # per user
    agent_ask_timeout_seconds: int = 600  # how long a run waits for your answer to its question
    tavily_api_key: str = ""  # optional web lookup for unfamiliar skills

    # ---- evaluation: which RAGAS API scores the answers: legacy (ragas.metrics, default) | collections (ragas.metrics.collections, ragas 0.4+) | auto
    eval_ragas_api: str = "legacy"

    # ---- evaluation: log every staffing / learning-path answer (with its question and contexts) for RAGAS
    eval_log_enabled: bool = True

    # ---- derived paths
    @property
    def data_path(self) -> Path:
        p = Path(self.data_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def upload_dir(self) -> Path:
        p = self.data_path / "uploads"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def cache_dir(self) -> Path:
        p = self.data_path / "cache"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def catalog_file(self) -> Path:
        return Path(self.course_catalog_path) if self.course_catalog_path else BACKEND_DIR / "app" / "data" / "course_catalog.csv"

    @property
    def course_source_list(self) -> list[str]:
        return [x.strip().lower() for x in self.course_sources.split(",") if x.strip()]

    @property
    def eval_dir(self) -> Path:
        p = self.data_path / "eval"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def eval_log_path(self) -> Path:
        return self.eval_dir / "interactions.jsonl"

    @property
    def db_path(self) -> Path:
        return self.data_path / "skillmatrix.db"

    @property
    def chroma_dir(self) -> str:
        return self.chroma_path or str(self.data_path / "chroma")

    @property
    def gptcache_ns(self) -> frozenset:
        return frozenset(n.strip() for n in self.gptcache_namespaces.split(",") if n.strip())


@lru_cache
def get_settings() -> Settings:
    return Settings()
