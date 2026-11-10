import unittest
from unittest.mock import patch

from app.schemas import LearningRequest, Requirement
from app.services import learning
from app.services.courses.base import Course


class LearningPlanConstraintTests(unittest.TestCase):
    def setUp(self) -> None:
        self.gaps = [
            {"skill": "Kubernetes", "current": 0, "target": 3, "delta": 3},
            {"skill": "Docker", "current": 3, "target": 4, "delta": 1},
        ]
        self.plan = learning.allocate_hours(self.gaps, weeks=4, hours_per_week=5)
        self.courses = {
            "Kubernetes": [{
                "title": "Kubernetes Basics",
                "url": "https://learn.example.test/kubernetes",
                "duration_hours": 10,
                "kind": "course",
            }],
            "Docker": [{
                "title": "Advanced Docker Masterclass: Course",
                "url": "https://learn.example.test/docker",
                "duration_hours": 20,
                "kind": "course",
            }],
        }

    def _plan_markdown(self, docker_warning: bool = True) -> str:
        schedule = "\n".join(learning.time_plan_lines(self.plan))
        warning = (
            "- Time check: this course takes about 20 h but only about 5 h are planned.\n"
            if docker_warning else ""
        )
        return f"""# Learning Path

## Summary
4-week plan at about 5 h/week (20 h in total)

## Skill Gaps
| Skill | Current Level | Target Level | Gap |
|---|---:|---:|---:|
| Kubernetes | 0 | 3 | 3 |
| Docker | 3 | 4 | 1 |

## Weekly Plan
{schedule}
Docker advanced masterclass course
{warning}
## Courses
[Kubernetes Basics](https://learn.example.test/kubernetes)
[Advanced Docker Masterclass: Course](https://learn.example.test/docker)
"""

    def test_accepts_plan_matching_computed_gaps_schedule_and_time_checks(self) -> None:
        problems = learning._validate_generated_plan(
            self._plan_markdown(), self.gaps, self.plan, self.courses, weeks=4, hours_per_week=5
        )

        self.assertEqual(problems, [])

    def test_rejects_mismatched_gap_values(self) -> None:
        markdown = self._plan_markdown().replace("| Kubernetes | 0 | 3 | 3 |", "| Kubernetes | 1 | 3 | 2 |")

        problems = learning._validate_generated_plan(markdown, self.gaps, self.plan, self.courses, 4, 5)

        self.assertTrue(any("Kubernetes" in problem for problem in problems))

    def test_rejects_unretrieved_links(self) -> None:
        markdown = self._plan_markdown().replace(
            "https://learn.example.test/docker", "https://unknown.example.test/course"
        )

        problems = learning._validate_generated_plan(markdown, self.gaps, self.plan, self.courses, 4, 5)

        self.assertTrue(any("not retrieved" in problem for problem in problems))

    def test_rejects_course_link_with_invented_title(self) -> None:
        markdown = self._plan_markdown().replace(
            "[Advanced Docker Masterclass: Course]", "[Unrelated course]"
        )

        problems = learning._validate_generated_plan(markdown, self.gaps, self.plan, self.courses, 4, 5)

        self.assertTrue(any("link label" in problem for problem in problems))

    def test_rejects_unrequested_skill_gaps(self) -> None:
        markdown = self._plan_markdown().replace(
            "| Docker | 3 | 4 | 1 |", "| Docker | 3 | 4 | 1 |\n| Rust | 0 | 3 | 3 |"
        )

        problems = learning._validate_generated_plan(markdown, self.gaps, self.plan, self.courses, 4, 5)

        self.assertTrue(any("unrequested skill: Rust" in problem for problem in problems))

    def test_rejects_sample_links_in_model_output(self) -> None:
        courses = {skill: [dict(course) for course in items] for skill, items in self.courses.items()}
        courses["Docker"][0]["is_sample"] = True

        problems = learning._validate_generated_plan(self._plan_markdown(), self.gaps, self.plan, courses, 4, 5)

        self.assertTrue(any("sample course link" in problem for problem in problems))

    def test_requires_disclosure_for_course_longer_than_its_allocation(self) -> None:
        problems = learning._validate_generated_plan(
            self._plan_markdown(docker_warning=False), self.gaps, self.plan, self.courses, 4, 5
        )

        self.assertTrue(any("Advanced Docker Masterclass" in problem for problem in problems))

    def test_learning_evaluation_context_includes_retrieved_course_details(self) -> None:
        employee = {"id": 1, "name": "Test Employee", "roles": [], "skills": {}}
        gaps = [{"skill": "React", "current": 3, "target": 4, "delta": 1}]
        plan = learning.allocate_hours(gaps, weeks=4, hours_per_week=5)
        courses = {
            "React": [{
                "title": "React Basics",
                "url": "https://learn.example.test/react",
                "description": "Build an interactive interface.",
                "learning_outcomes": ["Create components"],
                "kind": "course",
                "duration_hours": 5,
            }]
        }
        request = LearningRequest(
            employee_id=1,
            requirements=[Requirement(skill="React", level=4)],
            weeks=4,
            hours_per_week=5,
        )

        with (
            patch.object(learning.eval_log, "data_fingerprint", return_value="test"),
            patch.object(learning.eval_log, "log_interaction") as log_interaction,
        ):
            learning._log_plan(employee, gaps, courses, request, "plan", "llm", plan)

        contexts = log_interaction.call_args.args[3]
        self.assertTrue(any("Build an interactive interface." in context for context in contexts))
        self.assertTrue(any("Create components" in context for context in contexts))

    def test_build_keeps_model_plan_and_replaces_computed_sections(self) -> None:
        employee = {
            "id": 1,
            "name": "Test Employee",
            "roles": ["Developer"],
            "background": "",
            "skills": [
                {"canonical_skill": "Kubernetes", "rating": 0},
                {"canonical_skill": "Docker", "rating": 3},
            ],
        }
        request = LearningRequest(
            employee_id=1,
            requirements=[Requirement(skill="Kubernetes", level=3), Requirement(skill="Docker", level=4)],
            weeks=4,
            hours_per_week=5,
        )
        course_by_skill = {
            skill: [Course(id=skill, skill=skill, **course) for course in courses]
            for skill, courses in self.courses.items()
        }
        generated = self._plan_markdown().replace(
            "4-week plan at about 5 h/week (20 h in total)",
            "4-week plan at about 8 h/week (32 h in total)",
        ).replace(
            "| Kubernetes | 0 | 3 | 3 |",
            "| Kubernetes | 1 | 3 | 2 |",
        ).replace(
            "Weeks 1–3: Kubernetes (0 → 3), about 15 h",
            "Weeks 1–2: Kubernetes (0 → 3), about 10 h",
        )

        with (
            patch.object(learning.repo, "get_employee", return_value=employee),
            patch.object(learning, "check_input", side_effect=lambda text: text),
            patch.object(learning.groups, "effective", side_effect=lambda ratings, skill: (ratings.get(skill, 0), skill)),
            patch.object(learning, "_related_skills"),
            patch.object(learning, "search_courses", side_effect=lambda skill, level, limit: (course_by_skill[skill], [])),
            patch.object(learning.llm, "is_configured", return_value=True),
            patch.object(learning.orchestration, "run_learning_path", return_value={"markdown": generated}),
            patch.object(learning, "_log_plan"),
        ):
            result = learning.build(request, require_approval=True)

        self.assertEqual(result["mode"], "llm")
        self.assertTrue(result["approval_required"])
        self.assertEqual(result["computed_budget"], "4 weeks × 5 hours/week = 20 hours")
        self.assertEqual(result["computed_schedule"], learning.time_plan_lines(self.plan))
        self.assertIn("template_markdown", result)
        self.assertIn("4-week plan at about 5 h/week (20 h in total)", result["markdown"])
        self.assertIn("Weeks 1–3: Kubernetes (0 → 3), about 15 h", result["markdown"])
        self.assertNotIn("32 h in total", result["markdown"])
        self.assertNotIn("Weeks 1–2: Kubernetes", result["markdown"])
        self.assertIn("[Kubernetes Basics](https://learn.example.test/kubernetes)", result["markdown"])

    def test_build_uses_template_when_model_generation_fails(self) -> None:
        employee = {
            "id": 1,
            "name": "Test Employee",
            "roles": ["Developer"],
            "background": "",
            "skills": [
                {"canonical_skill": "Kubernetes", "rating": 0},
                {"canonical_skill": "Docker", "rating": 3},
            ],
        }
        request = LearningRequest(
            employee_id=1,
            requirements=[Requirement(skill="Kubernetes", level=3), Requirement(skill="Docker", level=4)],
            weeks=4,
            hours_per_week=5,
        )
        course_by_skill = {
            skill: [Course(id=skill, skill=skill, **course) for course in courses]
            for skill, courses in self.courses.items()
        }

        with (
            patch.object(learning.repo, "get_employee", return_value=employee),
            patch.object(learning, "check_input", side_effect=lambda text: text),
            patch.object(learning.groups, "effective", side_effect=lambda ratings, skill: (ratings.get(skill, 0), skill)),
            patch.object(learning, "_related_skills"),
            patch.object(learning, "search_courses", side_effect=lambda skill, level, limit: (course_by_skill[skill], [])),
            patch.object(learning.llm, "is_configured", return_value=True),
            patch.object(learning.orchestration, "run_learning_path", side_effect=RuntimeError("model unavailable")),
            patch.object(learning, "_log_plan"),
        ):
            result = learning.build(request)

        self.assertEqual(result["mode"], "template")
        self.assertFalse(result["approval_required"])
        self.assertTrue(any("could not be produced" in warning for warning in result["warnings"]))


if __name__ == "__main__":
    unittest.main()
