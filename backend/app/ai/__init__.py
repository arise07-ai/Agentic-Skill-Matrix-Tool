"""AI integration layer: every model-related call in the app goes through this package.

Start with AI_INTEGRATION.md. Raise `AINotConfigured` when a capability needs a model that is not set up;
callers catch it and fall back to a deterministic heuristic/template.
"""


class AINotConfigured(RuntimeError):
    """No LLM / embedding provider is configured (e.g. OPENAI_API_KEY is not set)."""
