import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.ai import cache


class CacheStatusTests(unittest.TestCase):
    def test_status_reports_memory_fallback_without_initializing_gptcache(self) -> None:
        settings = SimpleNamespace(redis_url="", gptcache_enabled=True)
        with (
            patch.object(cache, "get_settings", return_value=settings),
            patch.object(cache, "_get_redis") as get_redis,
            patch.object(cache, "_gpt", None),
            patch.object(cache, "_gpt_error", None),
        ):
            status = cache.cache_status()

        get_redis.assert_not_called()
        self.assertEqual(status["exact_cache"], "in-memory fallback")
        self.assertEqual(status["gptcache"], "enabled (initializes on first use)")

    def test_status_reports_shared_redis_cache(self) -> None:
        settings = SimpleNamespace(redis_url="redis://cache:6379/0", gptcache_enabled=False)
        with (
            patch.object(cache, "get_settings", return_value=settings),
            patch.object(cache, "_get_redis", return_value=object()),
        ):
            status = cache.cache_status()

        self.assertEqual(status["exact_cache"], "Redis shared")
        self.assertEqual(status["gptcache"], "disabled")

    def test_status_distinguishes_initialization_from_operation(self) -> None:
        settings = SimpleNamespace(redis_url="", gptcache_enabled=True)
        with (
            patch.object(cache, "get_settings", return_value=settings),
            patch.object(cache, "_get_redis", return_value=None),
            patch.object(cache, "_gpt", object()),
            patch.object(cache, "_gpt_operational", False),
            patch.object(cache, "_gpt_operation_error", None),
        ):
            status = cache.cache_status()

        self.assertEqual(status["gptcache"], "initialized (awaiting first operation)")


if __name__ == "__main__":
    unittest.main()
