import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.agents import intake


class IntakeRequirementCountTests(unittest.TestCase):
    def _propose(self, text: str, requirements: list[dict], answers: list[str] | None = None) -> dict:
        ctx = SimpleNamespace(
            input={"text": text},
            _steps=[{"kind": "answer", "text": answer} for answer in answers or []],
        )
        with (
            patch.object(intake.proposals, "create", return_value={"proposal_id": 1}) as create,
            patch.object(intake.tools, "skill_coverage", return_value={"match_method": "exact"}),
        ):
            intake._propose(ctx, {"title": "Project", "requirements": requirements})
        return create.call_args.args[3]

    def test_total_headcount_is_not_repeated_for_every_skill(self) -> None:
        payload = self._propose(
            "We need 5 people total across Python and React positions.",
            [
                {"skill": "Python", "min_people": 5},
                {"skill": "React", "min_people": 5},
            ],
        )

        self.assertEqual([requirement["min_people"] for requirement in payload["requirements"]], [1, 1])

    def test_explicit_per_skill_counts_are_preserved(self) -> None:
        payload = self._propose(
            "We need 2 Python developers and 3 people with React.",
            [
                {"skill": "Python", "min_people": 1},
                {"skill": "React", "min_people": 1},
            ],
        )

        self.assertEqual([requirement["min_people"] for requirement in payload["requirements"]], [2, 3])

    def test_answered_skill_count_is_used(self) -> None:
        payload = self._propose(
            "We need 5 people total across Python and React positions.",
            [
                {"skill": "Python", "min_people": 5},
                {"skill": "React", "min_people": 5},
            ],
            answers=["Two people must be proficient in React."],
        )

        self.assertEqual([requirement["min_people"] for requirement in payload["requirements"]], [1, 2])


if __name__ == "__main__":
    unittest.main()
