import json
import unittest
from unittest.mock import patch

from app.ai import evaluation
from app.ai.guardrails import GuardrailViolation
from app.schemas import LearningCoachMessage, LearningCoachRequest, LearningRequest, Requirement
from app.services import learning


class LearningCoachTests(unittest.TestCase):
    def setUp(self) -> None:
        self.employee = {
            "id": 7,
            "employee_no": "E007",
            "name": "Test Learner",
            "roles": ["Developer"],
            "background": "Builds web applications.",
            "domain_tags": [],
            "skills": [
                {"canonical_skill": "React", "rating": 3},
                {"canonical_skill": "TypeScript", "rating": 0},
            ],
        }
        self.request = LearningCoachRequest(
            employee_id=7,
            requirements=[
                {"skill": "React", "level": 4},
                {"skill": "TypeScript", "level": 3},
            ],
            weeks=4,
            hours_per_week=5,
            plan_markdown="# Learning Path\n\n4-week plan at about 5 h/week (20 h in total)",
            messages=[{"role": "user", "content": "Can these hours guarantee the target levels?"}],
        )

    def test_coach_uses_verified_schedule_and_logs_grounding_context(self) -> None:
        answer = "The schedule is 20 hours, but study time alone cannot guarantee either target."
        with (
            patch.object(learning.repo, "get_employee", return_value=self.employee),
            patch.object(learning.groups, "effective", side_effect=lambda ratings, skill: (ratings.get(skill, 0), skill)),
            patch.object(learning, "check_input", side_effect=lambda text: text),
            patch.object(learning, "check_output", return_value=(answer, [])),
            patch.object(learning.llm, "complete", return_value=answer) as complete,
            patch.object(learning.eval_log, "log_interaction") as log_interaction,
        ):
            result = learning.coach(self.request)

        self.assertEqual(result, {"answer": answer, "warnings": []})
        prompt_data = json.loads(complete.call_args.args[1])
        self.assertEqual(prompt_data["verified_context"]["time_budget"]["total_hours"], 20)
        self.assertEqual(
            prompt_data["verified_context"]["computed_schedule"],
            [
                "Weeks 1–3: TypeScript (0 → 3), about 15 h",
                "Week 4: React (3 → 4), about 5 h",
            ],
        )
        self.assertEqual(complete.call_args.kwargs["namespace"], "learning_coach")
        self.assertEqual(log_interaction.call_args.args[:3], (
            "learning_coach",
            "Can these hours guarantee the target levels?",
            answer,
        ))
        self.assertIn("Authoritative computed schedule", " ".join(log_interaction.call_args.args[3]))

    def test_coach_uses_recent_turns_to_answer_a_follow_up(self) -> None:
        request = LearningCoachRequest(
            employee_id=self.request.employee_id,
            requirements=self.request.requirements,
            weeks=self.request.weeks,
            hours_per_week=self.request.hours_per_week,
            plan_markdown=self.request.plan_markdown,
            messages=[
                {"role": "user", "content": "How should I split the study time?"},
                {"role": "assistant", "content": "The schedule gives TypeScript about 15 hours and React about 5."},
                {"role": "user", "content": "How do I make that practical?"},
            ],
        )
        answer = "Use the TypeScript block for a small implementation task, then apply React in a focused component."
        with (
            patch.object(learning.repo, "get_employee", return_value=self.employee),
            patch.object(learning.groups, "effective", side_effect=lambda ratings, skill: (ratings.get(skill, 0), skill)),
            patch.object(learning, "check_input", side_effect=lambda text: text),
            patch.object(learning, "check_output", return_value=(answer, [])),
            patch.object(learning.llm, "complete", return_value=answer) as complete,
            patch.object(learning.eval_log, "log_interaction") as log_interaction,
        ):
            result = learning.coach(request)

        self.assertEqual(result["answer"], answer)
        prompt_data = json.loads(complete.call_args.args[1])
        self.assertEqual(prompt_data["conversation"], [
            {"role": message.role, "content": message.content} for message in request.messages
        ])
        logged_question = log_interaction.call_args.args[1]
        self.assertIn("How should I split the study time?", logged_question)
        self.assertIn("How do I make that practical?", logged_question)

    def test_coach_closing_uses_cached_reply_without_calling_llm(self) -> None:
        request = self.request.model_copy(update={
            "messages": [LearningCoachMessage(role="user", content="Nothing else. Thanks")],
        })

        with (
            patch.object(learning.repo, "get_employee", return_value=self.employee) as get_employee,
            patch.object(learning, "check_input", side_effect=lambda text: text),
            patch.object(learning.llm, "complete") as complete,
            patch.object(learning, "_learning_gaps") as learning_gaps,
            patch.object(learning.eval_log, "log_interaction") as log_interaction,
        ):
            result = learning.coach(request)

        self.assertIn("learning plan", result["answer"])
        self.assertEqual(result["warnings"], [])
        complete.assert_not_called()
        learning_gaps.assert_not_called()
        get_employee.assert_not_called()
        log_interaction.assert_not_called()

    def test_coach_checks_guardrails_before_returning_cached_closing(self) -> None:
        request = self.request.model_copy(update={
            "messages": [LearningCoachMessage(role="user", content="Nothing else. Thanks")],
        })
        with (
            patch.object(learning.repo, "get_employee", return_value=self.employee) as get_employee,
            patch.object(learning, "check_input", side_effect=GuardrailViolation("Input rejected")),
            patch.object(learning.llm, "complete") as complete,
        ):
            with self.assertRaisesRegex(GuardrailViolation, "Input rejected"):
                learning.coach(request)

        get_employee.assert_not_called()
        complete.assert_not_called()

    def test_coach_requires_latest_message_to_be_from_learner(self) -> None:
        request = LearningCoachRequest(
            employee_id=self.request.employee_id,
            requirements=self.request.requirements,
            weeks=self.request.weeks,
            hours_per_week=self.request.hours_per_week,
            plan_markdown=self.request.plan_markdown,
            messages=[{"role": "assistant", "content": "Earlier reply"}],
        )
        with patch.object(learning.repo, "get_employee", return_value=self.employee):
            with self.assertRaisesRegex(GuardrailViolation, "learner message"):
                learning.coach(request)

    def test_jev_course_exclusions_are_returned_as_coach_notices(self) -> None:
        request = LearningRequest(
            employee_id=7,
            requirements=[Requirement(skill="React", level=4)],
            weeks=4,
            hours_per_week=5,
        )
        with (
            patch.object(learning.repo, "get_employee", return_value=self.employee),
            patch.object(learning, "check_input", side_effect=lambda text: text),
            patch.object(learning.groups, "effective", side_effect=lambda ratings, skill: (ratings.get(skill, 0), skill)),
            patch.object(learning, "_related_skills"),
            patch.object(
                learning, "search_courses",
                return_value=([], ["Jev excluded 2 course(s) that did not appear to teach React."]),
            ),
            patch.object(learning.llm, "is_configured", return_value=False),
            patch.object(learning, "_log_plan"),
        ):
            result = learning.build(request)

        self.assertEqual(result["warnings"], [])
        self.assertEqual(len(result["coach_notices"]), 1)
        self.assertIn("removed 2", result["coach_notices"][0])
        self.assertIn("for React", result["coach_notices"][0])
        self.assertIn("didn’t clearly teach it", result["coach_notices"][0])

    def test_golden_fixtures_include_a_separate_coach_category(self) -> None:
        self.assertIn("learning_coach", evaluation.RAGAS_FEATURES)
        self.assertEqual(evaluation.golden_check(), [])


if __name__ == "__main__":
    unittest.main()
