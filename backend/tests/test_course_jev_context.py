import csv
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.services.courses import _is_relevant
from app.services.courses import catalog, mslearn, youtube
from app.services.courses.base import Course


class JevCourseContextTests(unittest.TestCase):
    def test_description_and_outcomes_are_sent_to_jev(self) -> None:
        course = Course(
            id="sql-course",
            title="Database administration",
            url="https://example.test/sql",
            skill="PostgreSQL",
            description="Learn to query data with SQL.",
            learning_outcomes="Write SELECT, JOIN, and aggregate queries.",
        )
        with patch("app.services.courses.jev.decide", return_value=("YES", 0.9)) as decide:
            self.assertTrue(_is_relevant("SQL", course))

        state = decide.call_args.kwargs["state"]
        self.assertEqual(state["course_description"], "Learn to query data with SQL.")
        self.assertEqual(state["learning_outcomes"], "Write SELECT, JOIN, and aggregate queries.")

    def test_jev_course_context_is_bounded(self) -> None:
        course = Course(
            id="large-course",
            title="Python",
            url="https://example.test/python",
            skill="Python",
            description="d" * 1500,
            learning_outcomes="o" * 1500,
        )
        with patch("app.services.courses.jev.decide", return_value=("YES", 0.9)) as decide:
            _is_relevant("Python", course)

        state = decide.call_args.kwargs["state"]
        self.assertEqual(len(state["course_description"]), 1200)
        self.assertEqual(len(state["learning_outcomes"]), 1200)


class CourseDescriptionSourceTests(unittest.TestCase):
    def test_curated_catalog_loads_optional_description_and_outcomes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "courses.csv"
            with path.open("w", newline="", encoding="utf-8") as file:
                writer = csv.DictWriter(
                    file,
                    fieldnames=["skill", "title", "url", "description", "learning_outcomes"],
                )
                writer.writeheader()
                writer.writerow({
                    "skill": "SQL",
                    "title": "PostgreSQL Querying",
                    "url": "https://example.test/sql",
                    "description": "Query and manage relational data.",
                    "learning_outcomes": "Write SELECT and JOIN queries.",
                })
            catalog._load.cache_clear()
            try:
                with patch.object(catalog, "get_settings", return_value=SimpleNamespace(catalog_file=path)):
                    courses, skipped = catalog.load()
            finally:
                catalog._load.cache_clear()

        self.assertEqual(skipped, 0)
        self.assertEqual(courses[0].description, "Query and manage relational data.")
        self.assertEqual(courses[0].learning_outcomes, "Write SELECT and JOIN queries.")

    def test_microsoft_learn_summary_is_carried_as_description(self) -> None:
        record = {
            "uid": "sql-module",
            "url": "https://learn.example/sql",
            "title": "Query data with SQL",
            "kind": "module",
            "min": 60,
            "levels": ["beginner"],
            "products": [],
            "subjects": [],
            "summary": "Learn to query relational data.",
            "pop": 1,
            "rating": None,
        }
        with patch.object(mslearn, "_catalog", return_value=[record]):
            courses = mslearn.search("SQL", limit=1)

        self.assertEqual(courses[0].description, "Learn to query relational data.")

    def test_youtube_description_is_carried_from_cached_snippet(self) -> None:
        settings = SimpleNamespace(youtube_api_key="configured", youtube_cache_days=7)
        cached_video = {
            "id": "video-1",
            "title": "SQL query tutorial",
            "description": "Practice SELECT and JOIN queries.",
            "channel": "Example",
            "hours": 0.25,
        }
        with (
            patch.object(youtube, "get_settings", return_value=settings),
            patch.object(youtube.cache, "read", return_value=[cached_video]),
            patch.object(youtube.groups, "load", return_value={}),
            patch.object(youtube.groups, "match", return_value=None),
        ):
            courses = youtube.search("SQL", limit=1)

        self.assertEqual(courses[0].description, "Practice SELECT and JOIN queries.")


if __name__ == "__main__":
    unittest.main()
