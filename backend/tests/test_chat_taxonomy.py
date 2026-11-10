import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.agents import tools


class ChatTaxonomyToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.taxonomy = {
            "Apache Kafka": {"category": "Backend Frameworks", "aliases": ["kafka"]},
            "RabbitMQ": {"category": "Backend Frameworks", "aliases": []},
            "PostgreSQL": {"category": "Databases", "aliases": []},
        }

    def test_skill_overview_distinguishes_taxonomy_from_employee_coverage(self) -> None:
        with (
            patch.object(tools.standardization, "index", return_value=(self.taxonomy, {})),
            patch.object(tools.groups, "names", return_value=["Databases & Data"]),
            patch.object(tools.groups, "match", return_value=None),
            patch.object(tools.groups, "tools_of", return_value=[]),
            patch.object(tools.db, "rows", return_value=[
                {"skill": "Apache Kafka", "holders": 1, "avg_rating": 3.0},
            ]),
            patch.object(tools.db, "one", return_value={"n": 2}),
        ):
            result = tools.skill_overview(SimpleNamespace(), {"category": "Backend Frameworks"})

        self.assertTrue(result["category_found"])
        self.assertFalse(result["group_found"])
        self.assertEqual(
            [row["skill"] for row in result["skills"]],
            ["Apache Kafka", "RabbitMQ"],
        )
        self.assertEqual(result["skills"][0]["employees_with_skill"], 1)
        self.assertEqual(result["skills"][1]["employees_with_skill"], 0)

    def test_databases_category_is_retrievable(self) -> None:
        with (
            patch.object(tools.standardization, "index", return_value=(self.taxonomy, {})),
            patch.object(tools.groups, "names", return_value=["Databases & Data"]),
            patch.object(tools.groups, "match", return_value=None),
            patch.object(tools.groups, "tools_of", return_value=[]),
            patch.object(tools.db, "rows", return_value=[
                {"skill": "PostgreSQL", "holders": 2, "avg_rating": 4.0},
            ]),
            patch.object(tools.db, "one", return_value={"n": 2}),
        ):
            result = tools.skill_overview(SimpleNamespace(), {"category": "Databases"})

        self.assertTrue(result["category_found"])
        self.assertEqual(result["matched_taxonomy_category"], "Databases")
        self.assertEqual([row["skill"] for row in result["skills"]], ["PostgreSQL"])

    def test_who_has_skill_expands_database_group_and_returns_skill_evidence(self) -> None:
        ctx = SimpleNamespace(returned_ids=set())
        with (
            patch.object(tools.groups, "match", return_value="Databases"),
            patch.object(tools.groups, "tools_of", return_value=["SQL", "MongoDB", "PostgreSQL"]),
            patch.object(tools.db, "one", return_value={"n": 2}),
            patch.object(tools.db, "rows", return_value=[
                {"id": 7, "name": "Alex", "skill": "PostgreSQL", "rating": 4},
                {"id": 7, "name": "Alex", "skill": "SQL", "rating": 3},
                {"id": 8, "name": "Sam", "skill": "MongoDB", "rating": 5},
            ]),
        ):
            result = tools.who_has_skill(ctx, {"skill": "database", "min_level": 1, "limit": 2})

        self.assertEqual(result["skill_group"], "Databases")
        self.assertEqual(result["total_matching"], 2)
        self.assertEqual(result["people"], [
            {"id": 7, "name": "Alex", "skills": [
                {"skill": "PostgreSQL", "rating": 4},
                {"skill": "SQL", "rating": 3},
            ]},
            {"id": 8, "name": "Sam", "skills": [{"skill": "MongoDB", "rating": 5}]},
        ])
        self.assertEqual(ctx.returned_ids, {7, 8})

    def test_unknown_category_is_not_confused_with_an_empty_taxonomy(self) -> None:
        with (
            patch.object(tools.standardization, "index", return_value=(self.taxonomy, {})),
            patch.object(tools.groups, "names", return_value=["Databases & Data"]),
            patch.object(tools.groups, "match", return_value=None),
            patch.object(tools.groups, "tools_of", return_value=[]),
            patch.object(tools.db, "rows", return_value=[]),
            patch.object(tools.db, "one", return_value={"n": 0}),
        ):
            result = tools.skill_overview(SimpleNamespace(), {"category": "Messaging Systems"})

        self.assertFalse(result["category_found"])
        self.assertFalse(result["group_found"])
        self.assertEqual(result["skills"], [])
        self.assertIn("Backend Frameworks", result["available_taxonomy_categories"])
        self.assertNotIn("Messaging Systems", result["available_taxonomy_categories"])

    def test_related_skills_only_returns_configured_taxonomy_entries(self) -> None:
        with (
            patch.object(tools.standardization, "index", return_value=(self.taxonomy, {})),
            patch.object(tools.standardization, "normalize", return_value={
                "canonical": "Apache Kafka", "method": "exact",
            }),
            patch.object(tools.groups, "related_skills_of", return_value=["RabbitMQ", "Invented Skill"]),
        ):
            result = tools.related_skills(SimpleNamespace(), {"skill": "kafka"})

        self.assertTrue(result["known_in_taxonomy"])
        self.assertEqual(result["configured_related_skills"], ["RabbitMQ"])

    def test_no_configured_relationship_returns_empty_list(self) -> None:
        with (
            patch.object(tools.standardization, "index", return_value=(self.taxonomy, {})),
            patch.object(tools.standardization, "normalize", return_value={
                "canonical": "Apache Kafka", "method": "exact",
            }),
            patch.object(tools.groups, "related_skills_of", return_value=[]),
        ):
            result = tools.related_skills(SimpleNamespace(), {"skill": "kafka"})

        self.assertEqual(result["configured_related_skills"], [])


if __name__ == "__main__":
    unittest.main()
