from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.agents import chat
from app.agents.runtime import AgentError
from app.ai.guardrails import GuardrailViolation, ToxicInputViolation


class ChatGuardrailsTests(unittest.TestCase):
    def test_toxic_input_returns_a_chat_reply_without_calling_the_model(self) -> None:
        ctx = SimpleNamespace(returned_ids=set())
        inp = {"messages": [{"role": "user", "content": "abusive message"}]}
        reply = "Let's keep the conversation respectful. Tell me what you'd like help with, and I'll do my best."

        with (
            patch.object(chat, "check_input", side_effect=ToxicInputViolation(reply)),
            patch.object(chat, "loop") as model_loop,
        ):
            result = chat.run(ctx, inp)

        self.assertEqual(result, {"answer": reply, "employee_ids": [], "refused": True})  # the page leaves refused turns out of the history
        model_loop.assert_not_called()

    def test_non_toxic_guardrail_violations_remain_errors(self) -> None:
        ctx = SimpleNamespace(returned_ids=set())
        inp = {"messages": [{"role": "user", "content": "prompt injection"}]}

        with patch.object(chat, "check_input", side_effect=GuardrailViolation("Input rejected")):
            with self.assertRaisesRegex(AgentError, "Input rejected"):
                chat.run(ctx, inp)

    def test_closing_message_still_passes_through_input_guardrails(self) -> None:
        ctx = SimpleNamespace(returned_ids=set())
        inp = {"messages": [{"role": "user", "content": "Nothing else. Thanks"}]}

        with (
            patch.object(chat, "check_input", side_effect=GuardrailViolation("Input rejected")),
            patch.object(chat, "loop") as model_loop,
        ):
            with self.assertRaisesRegex(AgentError, "Input rejected"):
                chat.run(ctx, inp)

        model_loop.assert_not_called()


if __name__ == "__main__":
    unittest.main()
