import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.agents import chat
from app.ai.conversation import cached_conversational_reply, contextual_question, normalize_messages


class ConversationContextTests(unittest.TestCase):
    def test_closing_messages_use_cached_replies_but_questions_do_not(self) -> None:
        self.assertIn("You’re welcome", cached_conversational_reply("Nothing else. Thanks!", "chat"))
        self.assertIn("learning plan", cached_conversational_reply("No, that's all, thank you.", "learning_coach"))
        self.assertIsNone(cached_conversational_reply("Thanks, who has Python?", "chat"))

    def test_contextual_question_includes_recent_turns_and_latest_request(self) -> None:
        messages = [
            {"role": "user", "content": "Who has Python at level 4?"},
            {"role": "assistant", "content": "Jane Smith and David Okafor."},
            {"role": "user", "content": "Who has related skills to that?"},
        ]

        question = contextual_question(messages)

        self.assertIn("Who has Python at level 4?", question)
        self.assertIn("Jane Smith and David Okafor.", question)
        self.assertIn("Who has related skills to that?", question)

    def test_history_is_limited_and_invalid_roles_are_ignored(self) -> None:
        messages = [{"role": "user", "content": str(i)} for i in range(14)]
        messages.insert(2, {"role": "system", "content": "not a conversation turn"})

        normalized = normalize_messages(messages)

        self.assertEqual(len(normalized), 12)
        self.assertEqual(normalized[0]["content"], "2")
        self.assertEqual(normalized[-1]["content"], "13")

    def test_chat_passes_recent_history_to_agent_and_logs_contextual_question(self) -> None:
        ctx = SimpleNamespace(returned_ids=set(), state={})
        history = [
            {"role": "user", "content": "Who has Python at level 4?"},
            {"role": "assistant", "content": "Jane Smith and David Okafor."},
            {"role": "user", "content": "Who has related skills to that?"},
        ]
        inp = {"messages": history}
        answer = "Jane Smith has related skills."

        with (
            patch.object(chat, "check_input", side_effect=lambda text: text),
            patch.object(chat, "loop", return_value=answer) as model_loop,
            patch.object(chat, "_log_for_evaluation") as log_for_evaluation,
        ):
            result = chat.run(ctx, inp)

        self.assertEqual(result["answer"], answer)
        self.assertEqual(model_loop.call_args.args[2][1:], history)
        logged_question = log_for_evaluation.call_args.args[2]
        self.assertIn(history[0]["content"], logged_question)
        self.assertIn(history[-1]["content"], logged_question)

    def test_chat_closing_uses_cached_reply_without_calling_agent_or_evaluation(self) -> None:
        ctx = SimpleNamespace(returned_ids=set(), state={})
        inp = {"messages": [{"role": "user", "content": "Nothing else. Thanks"}]}

        with (
            patch.object(chat, "check_input", side_effect=lambda text: text),
            patch.object(chat, "loop") as model_loop,
            patch.object(chat, "_log_for_evaluation") as log_for_evaluation,
        ):
            result = chat.run(ctx, inp)

        self.assertIn("You’re welcome", result["answer"])
        self.assertEqual(result["employee_ids"], [])
        model_loop.assert_not_called()
        log_for_evaluation.assert_not_called()


if __name__ == "__main__":
    unittest.main()
