import unittest
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, patch

from app.ai import jev, predict
from app.services import courses
from app.services.courses.base import Course


class JevRiskTests(unittest.TestCase):
    def test_each_jev_band_maps_to_configured_score_without_confidence_gate(self) -> None:
        settings = SimpleNamespace(typesafe_api_key="configured")
        for band, expected in (("LOW", 0.2), ("MEDIUM", 0.5), ("HIGH", 0.8)):
            with self.subTest(band=band):
                with (
                    patch.object(predict, "get_settings", return_value=settings),
                    patch.object(predict.jev, "decide", return_value=(band, 0.01)),
                    patch.object(predict.llm, "is_configured", return_value=False),
                ):
                    risk = predict.predict_risk(
                        level=4, best_rating=2, coverage_ratio=0.25, near_count=1, upskill_count=2
                    )

            self.assertEqual(risk, expected)

    def test_jev_failure_falls_back_to_llm_then_formula(self) -> None:
        settings = SimpleNamespace(typesafe_api_key="configured")
        with (
            patch.object(predict, "get_settings", return_value=settings),
            patch.object(predict.jev, "decide", side_effect=RuntimeError("service unavailable")),
            patch.object(predict.llm, "is_configured", return_value=True),
            patch.object(predict.llm, "complete_json", return_value={"risk": 0.41}),
        ):
            risk = predict.predict_risk(
                level=4, best_rating=2, coverage_ratio=0.25, near_count=1, upskill_count=2
            )

        self.assertEqual(risk, 0.41)


class JevCourseRelevanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.course = Course(
            id="course-1",
            title="Python Fundamentals",
            url="https://example.test/python",
            skill="Python",
            provider="Catalog",
        )

    def test_only_jev_verified_courses_are_returned(self) -> None:
        settings = SimpleNamespace(course_source_list=["catalog"], typesafe_api_key="configured")
        with (
            patch.object(courses, "get_settings", return_value=settings),
            patch.object(courses, "_run", return_value=[self.course]),
            patch.object(courses, "_is_relevant", return_value=True),
        ):
            found, notes = courses.search_courses("Python")

        self.assertEqual([course.id for course in found], ["course-1"])
        self.assertEqual(notes, [])

    def test_courses_are_omitted_when_jev_is_not_configured(self) -> None:
        settings = SimpleNamespace(course_source_list=["catalog"], typesafe_api_key="")
        with (
            patch.object(courses, "get_settings", return_value=settings),
            patch.object(courses, "_run", return_value=[self.course]),
            patch.object(courses, "_is_relevant") as relevance_check,
        ):
            found, notes = courses.search_courses("Python")

        self.assertEqual(found, [])
        self.assertIn("TYPESAFE_API_KEY", notes[0])
        relevance_check.assert_not_called()

    def test_rejected_courses_are_not_returned(self) -> None:
        settings = SimpleNamespace(course_source_list=["catalog"], typesafe_api_key="configured")
        with (
            patch.object(courses, "get_settings", return_value=settings),
            patch.object(courses, "_run", return_value=[self.course]),
            patch.object(courses, "_is_relevant", return_value=False),
        ):
            found, notes = courses.search_courses("Python")

        self.assertEqual(found, [])
        self.assertIn("excluded 1 course", notes[0])


class JevDecisionClientTests(unittest.TestCase):
    def test_decide_uses_a_fixed_choice_and_returns_validated_confidence(self) -> None:
        settings = SimpleNamespace(typesafe_api_key="configured")
        sdk = ModuleType("typesafe_sdk")
        sdk.Choice = lambda **kwargs: SimpleNamespace(**kwargs)
        sdk.TypeSafeClient = MagicMock()
        with (
            patch.object(jev, "get_settings", return_value=settings),
            patch.dict(sys.modules, {"typesafe_sdk": sdk}),
        ):
            context = sdk.TypeSafeClient.return_value
            client = context.__enter__.return_value
            client.system_one.return_value = SimpleNamespace(
                choices={"answer": SimpleNamespace(choice="YES", confidence=0.83)}
            )
            result = jev.decide(
                state={"item": "course"},
                question="answer",
                instructions="Choose yes or no.",
                options=("YES", "NO"),
            )

        self.assertEqual(result, ("YES", 0.83))
        self.assertEqual(
            client.system_one.call_args.kwargs["questions"]["answer"].criteria,
            {"YES": None, "NO": None},
        )

    def test_decide_rejects_choices_outside_the_fixed_list(self) -> None:
        settings = SimpleNamespace(typesafe_api_key="configured")
        sdk = ModuleType("typesafe_sdk")
        sdk.Choice = lambda **kwargs: SimpleNamespace(**kwargs)
        sdk.TypeSafeClient = MagicMock()
        with (
            patch.object(jev, "get_settings", return_value=settings),
            patch.dict(sys.modules, {"typesafe_sdk": sdk}),
        ):
            context = sdk.TypeSafeClient.return_value
            client = context.__enter__.return_value
            client.system_one.return_value = SimpleNamespace(
                choices={"answer": SimpleNamespace(choice="MAYBE", confidence=0.5)}
            )
            with self.assertRaisesRegex(ValueError, "outside the allowed options"):
                jev.decide(
                    state={},
                    question="answer",
                    instructions="Choose yes or no.",
                    options=("YES", "NO"),
                )


if __name__ == "__main__":
    unittest.main()
