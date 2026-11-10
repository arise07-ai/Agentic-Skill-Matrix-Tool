import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.agents.runtime import AgentError, Tool, _run_tool


class AgentToolErrorHandlingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ctx = SimpleNamespace(
            state={},
            step=lambda *_args: None,
        )

    def test_tool_exception_returns_standard_safe_payload_and_logs_details(self) -> None:
        tool = Tool(
            "lookup",
            "Test lookup",
            {"type": "object"},
            lambda *_args: (_ for _ in ()).throw(RuntimeError("private database detail")),
        )
        with patch("app.agents.runtime.log.exception") as log_exception:
            result = _run_tool(self.ctx, {"lookup": tool}, "lookup", {})

        payload = json.loads(result)
        self.assertEqual(payload["status"], "unavailable")
        self.assertIsNone(payload["data"])
        self.assertNotIn("private database detail", result)
        self.assertEqual(self.ctx.state["tool_log"][0]["result"], result)
        log_exception.assert_called_once()

    def test_unknown_tool_returns_same_safe_payload(self) -> None:
        result = _run_tool(self.ctx, {}, "missing", {})
        self.assertEqual(json.loads(result)["status"], "unavailable")
        self.assertNotIn("missing", result)

    def test_agent_error_is_preserved_for_intentional_agent_control_flow(self) -> None:
        tool = Tool(
            "ask_user",
            "Ask the user",
            {"type": "object"},
            lambda *_args: (_ for _ in ()).throw(AgentError("User response timed out")),
        )
        with self.assertRaisesRegex(AgentError, "User response timed out"):
            _run_tool(self.ctx, {"ask_user": tool}, "ask_user", {})


if __name__ == "__main__":
    unittest.main()
