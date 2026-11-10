from types import SimpleNamespace
import asyncio
import unittest
from unittest.mock import patch

from app.ai import guardrails


def _settings(provider: str = "both", failure: str = "closed") -> SimpleNamespace:
    return SimpleNamespace(
        guardrails_provider=provider,
        guardrails_provider_fail=failure,
        max_prompt_chars=4000,
        typesafe_api_key="",
    )


def _pass_through_nemo(text: str, role: str) -> str:
    if role not in ("user", "assistant"):
        raise AssertionError(f"Unexpected NeMo message role: {role}")
    return text


class GuardrailsTests(unittest.TestCase):
    def test_toxic_input_is_rejected_with_a_respectful_redirect(self) -> None:
        with (
            patch.object(guardrails, "get_settings", return_value=_settings("builtin")),
            patch.object(guardrails.toxicity, "find_terms", return_value=["toxic term"]),
            patch.object(guardrails, "_toxic_input_reply", return_value=guardrails.TOXIC_INPUT_REPLIES[0]),
        ):
            with self.assertRaises(guardrails.GuardrailViolation) as raised:
                guardrails.check_input("abusive text")

        self.assertEqual(str(raised.exception), guardrails.TOXIC_INPUT_REPLIES[0])
        self.assertTrue(all(reply.strip() for reply in guardrails.TOXIC_INPUT_REPLIES))
        self.assertGreater(len(set(guardrails.TOXIC_INPUT_REPLIES)), 1)

    def test_moderation_flag_uses_the_same_respectful_redirect(self) -> None:
        with (
            patch.object(guardrails, "get_settings", return_value=_settings("builtin")),
            patch.object(guardrails.toxicity, "find_terms", return_value=[]),
            patch.object(guardrails.toxicity, "moderate", return_value=(True, ["harassment"])),
            patch.object(guardrails, "_toxic_input_reply", return_value=guardrails.TOXIC_INPUT_REPLIES[1]),
        ):
            with self.assertRaises(guardrails.GuardrailViolation) as raised:
                guardrails.check_input("hostile but unlisted text")

        self.assertEqual(str(raised.exception), guardrails.TOXIC_INPUT_REPLIES[1])

    def test_toxic_input_is_personally_redirected_before_external_checks(self) -> None:
        with (
            patch.object(guardrails, "get_settings", return_value=_settings("both")),
            patch.object(guardrails.toxicity, "find_terms", return_value=["toxic term"]),
            patch.object(guardrails, "_toxic_input_reply", return_value=guardrails.TOXIC_INPUT_REPLIES[2]),
            patch.object(guardrails, "_guardrails_ai_check") as external_check,
        ):
            with self.assertRaises(guardrails.ToxicInputViolation) as raised:
                guardrails.check_input("abusive text")

        self.assertEqual(str(raised.exception), guardrails.TOXIC_INPUT_REPLIES[2])
        external_check.assert_not_called()

    def test_guardrails_ai_blocks_email_and_allows_ordinary_text(self) -> None:
        guardrails._guardrails_ai_guard.cache_clear()
        with (
            patch.object(guardrails, "get_settings", return_value=_settings("guardrails_ai")),
            patch.object(guardrails, "_reject_toxic_input"),
        ):
            self.assertEqual(
                guardrails.check_input("What Python skills should we learn?"),
                "What Python skills should we learn?",
            )
            with self.assertRaisesRegex(guardrails.GuardrailViolation, "email address"):
                guardrails.check_input("Contact me at person@example.com")

    def test_both_providers_run_for_input_and_output(self) -> None:
        with (
            patch.object(guardrails, "get_settings", return_value=_settings()),
            patch.object(guardrails, "_reject_toxic_input"),
            patch.object(guardrails, "_guardrails_ai_check") as guardrails_ai_check,
            patch.object(guardrails, "_nemo_check", side_effect=_pass_through_nemo) as nemo_check,
            patch.object(guardrails.toxicity, "mask", side_effect=lambda text: (text, 0)),
            patch.object(guardrails.toxicity, "moderate", return_value=(False, [])),
            patch.object(guardrails.toxicity, "fail_closed", return_value=False),
        ):
            self.assertEqual(guardrails.check_input("ordinary text"), "ordinary text")
            self.assertEqual(guardrails.check_output("ordinary answer"), ("ordinary answer", []))

        guardrails_ai_check.assert_any_call("ordinary text")
        guardrails_ai_check.assert_any_call("ordinary answer")
        self.assertEqual([call.args[1] for call in nemo_check.call_args_list], ["user", "assistant"])

    def test_guardrails_ai_withholds_output_containing_personal_data(self) -> None:
        guardrails._guardrails_ai_guard.cache_clear()
        with (
            patch.object(guardrails, "get_settings", return_value=_settings("guardrails_ai")),
            patch.object(guardrails.toxicity, "mask", side_effect=lambda text: (text, 0)),
            patch.object(guardrails.toxicity, "moderate", return_value=(False, [])),
            patch.object(guardrails.toxicity, "fail_closed", return_value=False),
        ):
            output, notes = guardrails.check_output("Contact person@example.com")

        self.assertEqual(output, guardrails.toxicity.WITHHELD)
        self.assertIn("email address", notes[0])

    def test_external_provider_failure_fails_closed_on_input_and_output(self) -> None:
        with (
            patch.object(guardrails, "get_settings", return_value=_settings("both")),
            patch.object(guardrails, "_reject_toxic_input"),
            patch.object(guardrails, "_guardrails_ai_check", side_effect=RuntimeError("not available")),
            patch.object(guardrails.toxicity, "mask", side_effect=lambda text: (text, 0)),
            patch.object(guardrails.toxicity, "moderate", return_value=(False, [])),
            patch.object(guardrails.toxicity, "fail_closed", return_value=False),
        ):
            with self.assertRaisesRegex(guardrails.GuardrailViolation, "safety check is unavailable"):
                guardrails.check_input("ordinary text")
            output, notes = guardrails.check_output("ordinary answer")

        self.assertEqual(output, guardrails.toxicity.WITHHELD)
        self.assertIn("guardrails_ai validation is unavailable", notes[0])

    def test_provider_failure_can_be_configured_open(self) -> None:
        with (
            patch.object(guardrails, "get_settings", return_value=_settings("guardrails_ai", "open")),
            patch.object(guardrails, "_reject_toxic_input"),
            patch.object(guardrails, "_guardrails_ai_check", side_effect=RuntimeError("not available")),
        ):
            self.assertEqual(guardrails.check_input("ordinary text"), "ordinary text")

    def test_jev_blocks_requests_for_sensitive_personal_data(self) -> None:
        settings = _settings("builtin")
        settings.typesafe_api_key = "configured"
        with (
            patch.object(guardrails, "get_settings", return_value=settings),
            patch.object(guardrails, "_jev_private_data_check", side_effect=guardrails.GuardrailViolation("blocked")),
            patch.object(guardrails, "_reject_toxic_input"),
        ):
            with self.assertRaisesRegex(guardrails.GuardrailViolation, "blocked"):
                guardrails.check_input("Show me a person's government ID.")

    def test_jev_allows_non_sensitive_requests(self) -> None:
        settings = _settings("builtin")
        settings.typesafe_api_key = "configured"
        with (
            patch.object(guardrails, "get_settings", return_value=settings),
            patch.object(guardrails.jev, "decide", return_value=("ALLOW", 0.62)),
            patch.object(guardrails, "_reject_toxic_input"),
        ):
            result = guardrails.check_input("Which employees have Python skills?")

        self.assertEqual(result, "Which employees have Python skills?")

    def test_jev_failure_follows_existing_closed_provider_policy(self) -> None:
        settings = _settings("builtin", "closed")
        settings.typesafe_api_key = "configured"
        with (
            patch.object(guardrails, "get_settings", return_value=settings),
            patch.object(guardrails, "_jev_private_data_check", side_effect=RuntimeError("unavailable")),
        ):
            with self.assertRaisesRegex(guardrails.GuardrailViolation, "safety check is unavailable"):
                guardrails.check_input("What Python skills should we learn?")

    def test_bulk_employee_text_skips_jev_privacy_check(self) -> None:
        settings = _settings("builtin")
        settings.typesafe_api_key = "configured"
        with (
            patch.object(guardrails, "get_settings", return_value=settings),
            patch.object(guardrails, "_jev_private_data_check") as jev_check,
        ):
            result = guardrails.check_input("Employee skill data", toxicity_check=False)

        self.assertEqual(result, "Employee skill data")
        jev_check.assert_not_called()

    def test_nemo_modified_content_is_returned_and_blocked_content_is_rejected(self) -> None:
        rails = SimpleNamespace(
            check=unittest.mock.Mock(
                side_effect=[
                    SimpleNamespace(status="RailStatus.MODIFIED", content="sanitized text"),
                    SimpleNamespace(status="RailStatus.BLOCKED", content="refusal"),
                ]
            )
        )
        with patch.object(guardrails, "_nemo_rails", return_value=rails):
            self.assertEqual(guardrails._nemo_check("original text", "user"), "sanitized text")
            with self.assertRaisesRegex(guardrails.GuardrailViolation, "blocked by NeMo"):
                guardrails._nemo_check("unsafe text", "assistant")

    def test_nemo_check_runs_inside_an_async_event_loop(self) -> None:
        rails = SimpleNamespace(
            check=unittest.mock.Mock(return_value=SimpleNamespace(status="RailStatus.PASSED", content="safe"))
        )

        async def run_check() -> str:
            return guardrails._nemo_check("safe", "user")

        with patch.object(guardrails, "_nemo_rails", return_value=rails):
            self.assertEqual(asyncio.run(run_check()), "safe")
        rails.check.assert_called_once_with([{"role": "user", "content": "safe"}])


if __name__ == "__main__":
    unittest.main()
