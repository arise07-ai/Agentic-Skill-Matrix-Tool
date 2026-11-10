import json
import sqlite3
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app import db
from app.schemas import ProjectInput, Requirement
from app.services import groups, learning, projects, standardization
from app.services.parsing import common


class OperationalRequirementTests(unittest.TestCase):
    def test_confirmed_task_mappings_and_context_only_phrases(self) -> None:
        self.assertEqual(projects._mapped_task_skills("executing remediation scripts"), ["Python", "Shell Scripting"])
        self.assertEqual(projects._mapped_task_skills("diagnosing system alerts"), ["Linux", "Shell Scripting"])

        result = {"requirements": [
            {"skill": "Python", "level": 3, "weight": 1, "min_people": 1, "domain_tags": []},
            {"skill": "IT service desk operations"},
            {"skill": "infrastructure troubleshooting"},
            {"skill": "legacy batch job failure resolution"},
        ]}
        projects._apply_operational_rules(
            result,
            "Python automation for legacy batch job failure resolution, IT service desk operations, "
            "infrastructure troubleshooting, diagnosing system alerts and executing remediation scripts",
        )

        by_skill = {r["skill"]: r for r in result["requirements"]}
        self.assertEqual(set(by_skill), {"Python", "Linux", "Shell Scripting"})
        self.assertEqual(by_skill["Python"]["domain_tags"], ["Mainframe/Batch Ops"])
        self.assertEqual(by_skill["Linux"]["domain_tags"], ["Mainframe/Batch Ops"])
        self.assertEqual(by_skill["Shell Scripting"]["domain_tags"], ["Mainframe/Batch Ops"])

        context_only = {"requirements": [{"skill": "Python", "domain_tags": []}, {"skill": "IT service desk operations"}]}
        projects._apply_operational_rules(context_only, "Python for IT service desk operations")
        self.assertEqual(context_only["requirements"], [{"skill": "Python", "domain_tags": []}])

    def test_extraction_converts_task_and_domain_labels_before_staffing(self) -> None:
        taxonomy = {
            "Python": {"category": "Programming", "aliases": []},
            "Shell Scripting": {"category": "Cloud & DevOps", "aliases": []},
        }
        with (
            patch.object(projects, "check_input", side_effect=lambda text: text),
            patch.object(projects.standardization, "index", return_value=(taxonomy, {})),
            patch.object(projects.tasks, "extract_requirements", return_value={"requirements": [
                {"skill": "executing remediation scripts", "level": 4},
                {"skill": "Mainframe/Batch Ops"},
            ], "headcount": 1, "roles": []}),
            patch.object(projects.groups, "match", return_value=None),
            patch.object(projects.standardization, "normalize", side_effect=lambda skill: {"canonical": skill}),
            patch.object(projects, "_known_lookup", return_value=(taxonomy, {"python": "Python", "shell scripting": "Shell Scripting"})),
            patch.object(projects, "_note_unknown"),
        ):
            result = projects.extract_requirements(
                "Python automation for legacy batch job failure resolution while executing remediation scripts"
            )

        requirements = {r["skill"]: r for r in result["requirements"]}
        self.assertEqual(set(requirements), {"Python", "Shell Scripting"})
        self.assertEqual(requirements["Python"]["level"], 4)
        self.assertEqual(requirements["Python"]["domain_tags"], ["Mainframe/Batch Ops"])
        self.assertEqual(requirements["Shell Scripting"]["domain_tags"], ["Mainframe/Batch Ops"])

    def test_domain_tag_matching_is_case_and_separator_insensitive(self) -> None:
        employee = {"domain_tags": ["Mainframe / Batch Ops"]}
        self.assertTrue(projects._has_domain_tags(employee, ["mainframe/batch ops"]))
        self.assertFalse(projects._has_domain_tags(employee, ["Mainframe"]))

    def test_import_parser_reads_explicit_domain_tags(self) -> None:
        records, warnings = common.build_records(
            [{"Employee": "A. Person", "Employee No": "E001", "Skills": "Python (4)",
              "Domain Tags": "Mainframe/Batch Ops; Payments"}],
            {"name": "Employee", "employee_no": "Employee No", "skills": "Skills", "domain_tags": "Domain Tags"},
            "wide",
        )
        self.assertFalse(warnings)
        self.assertEqual(records[0]["domain_tags"], ["Mainframe/Batch Ops", "Payments"])
        self.assertTrue(records[0]["domain_tags_provided"])

    def test_domain_tag_is_a_hard_filter_for_gap_and_staffing(self) -> None:
        employees = [
            {"id": 1, "employee_no": "E1", "name": "Tagged", "background": "", "roles": [],
             "domain_tags": ["Mainframe/Batch Ops"], "skills": {"Python": {"rating": 4, "category": "Programming"}}},
            {"id": 2, "employee_no": "E2", "name": "Untagged", "background": "", "roles": [],
             "domain_tags": [], "skills": {"Python": {"rating": 5, "category": "Programming"}}},
        ]
        project = ProjectInput(requirements=[
            Requirement(skill="Python", level=3, domain_tags=["Mainframe / Batch Ops"]),
        ])
        settings = SimpleNamespace(
            adjacency_min_similarity=0.75,
            staffing_weight_skill=0.7,
            staffing_weight_semantic=0.2,
            staffing_weight_role=0.1,
        )
        with (
            patch.object(projects, "_note_unknown"),
            patch.object(projects.repo, "all_profiles", return_value=employees),
            patch.object(projects, "get_settings", return_value=settings),
            patch.object(projects, "_skill_vectors", return_value={}),
            patch.object(projects.predict, "predict_risk", return_value=0.1),
        ):
            gaps = projects.gap_report(project)
        self.assertEqual([holder["id"] for holder in gaps["items"][0]["holders"]], [1])

        with (
            patch.object(projects, "_note_unknown"),
            patch.object(projects.repo, "all_profiles", return_value=employees),
            patch.object(projects, "get_settings", return_value=settings),
            patch.object(projects.vectorstore, "query", side_effect=RuntimeError("vector store unavailable")),
            patch.object(projects, "_log_staffing"),
        ):
            staffing = projects.staffing(project)
        self.assertEqual(staffing["team"], [1])
        candidates = {c["id"]: c for c in staffing["candidates"]}
        self.assertEqual([m["skill"] for m in candidates[1]["matched"]], ["Python"])
        self.assertEqual(candidates[2]["matched"], [])

    def test_matching_role_fills_team_slot_and_prioritizes_upskilling_below_target(self) -> None:
        employee = {
            "id": 1,
            "employee_no": "E1007",
            "name": "Gio Ramos",
            "background": "",
            "roles": ["Full Stack Developer"],
            "domain_tags": [],
            "skills": {
                "GraphQL": {"rating": 2, "category": "Backend"},
                "React": {"rating": 3, "category": "Frontend"},
            },
        }
        project = ProjectInput(
            requirements=[Requirement(skill="Full-Stack Development", level=3)],
            headcount=1,
        )
        settings = SimpleNamespace(
            adjacency_min_similarity=0.6,
            staffing_weight_skill=0.6,
            staffing_weight_semantic=0.3,
            staffing_weight_role=0.1,
        )
        with (
            patch.object(projects, "_note_unknown"),
            patch.object(projects.repo, "all_profiles", return_value=[employee]),
            patch.object(projects, "get_settings", return_value=settings),
            patch.object(projects.vectorstore, "query_with_documents", side_effect=RuntimeError("vector store unavailable")),
            patch.object(projects, "_skill_vectors", return_value={}),
            patch.object(projects.learning, "short_title", return_value="Web application"),
            patch.object(projects.learning, "build", return_value={"markdown": "plan"}),
            patch.object(projects, "_log_staffing"),
        ):
            staffing = projects.staffing(project)

        self.assertEqual(staffing["team"], [1])
        self.assertEqual(staffing["uncovered"], ["Full-Stack Development"])
        self.assertEqual(staffing["team_details"][0]["adds"], ["Full-Stack Development"])
        self.assertEqual(staffing["team_details"][0]["role_matches"], ["Full-Stack Development"])
        self.assertEqual(staffing["team_details"][0]["below_target"], ["Full-Stack Development"])
        self.assertEqual(staffing["learning"][0]["employee"]["name"], "Gio Ramos")
        self.assertEqual(staffing["learning"][0]["skills"][0]["current"], 2)
        self.assertIn("matching role", staffing["learning"][0]["skills"][0]["basis"])

    def test_specific_leadership_requirement_uses_leadership_rating_for_staffing_and_learning(self) -> None:
        ratings = {"Leadership": {"rating": 3, "category": "Soft Skills & Leadership"},
                   "Communication": {"rating": 5, "category": "Soft Skills & Leadership"}}
        employee = {
            "id": 1,
            "employee_no": "E1",
            "name": "Sarah",
            "background": "",
            "roles": [],
            "domain_tags": [],
            "skills": ratings,
        }
        project = ProjectInput(requirements=[Requirement(skill="Leadership", level=5)], headcount=1)
        settings = SimpleNamespace(
            adjacency_min_similarity=0.75,
            staffing_weight_skill=0.7,
            staffing_weight_semantic=0.2,
            staffing_weight_role=0.1,
        )
        with (
            patch.object(projects, "_note_unknown"),
            patch.object(projects.repo, "all_profiles", return_value=[employee]),
            patch.object(projects, "get_settings", return_value=settings),
            patch.object(projects.vectorstore, "query", side_effect=RuntimeError("vector store unavailable")),
            patch.object(projects, "_learning_suggestions", return_value=[]),
            patch.object(projects.eval_log, "data_fingerprint", return_value="test"),
            patch.object(projects.eval_log, "log_interaction") as log_interaction,
        ):
            staffing = projects.staffing(project)

        self.assertEqual(staffing["team"], [])
        self.assertEqual(staffing["uncovered"], ["Leadership"])
        self.assertEqual(staffing["nobody"], ["Leadership"])
        self.assertIn("No employee in the company reaches the requested level for: Leadership.",
                      log_interaction.call_args.args[2])

        with (
            patch.object(projects, "_note_unknown"),
            patch.object(projects.repo, "all_profiles", return_value=[employee]),
            patch.object(projects, "get_settings", return_value=settings),
            patch.object(projects.vectorstore, "query", side_effect=RuntimeError("vector store unavailable")),
            patch.object(projects.eval_log, "data_fingerprint", return_value="test"),
            patch.object(projects.eval_log, "log_interaction") as log_interaction,
        ):
            projects.staffing(ProjectInput(requirements=[Requirement(skill="Leadership", level=3)], headcount=1))
        self.assertIn("Verified ratings for Sarah: Leadership rated 3/5 (needs 3).",
                      log_interaction.call_args.args[2])

        learning_employee = {
            **employee,
            "skills": [
                {"canonical_skill": skill, "rating": detail["rating"]}
                for skill, detail in ratings.items()
            ],
        }
        gaps = learning._learning_gaps(
            learning_employee,
            [Requirement(skill="Leadership", level=5)],
        )

        self.assertEqual(gaps, [{"skill": "Leadership", "current": 3, "target": 5, "delta": 2}])

    def test_explicit_related_skills_appear_as_upskilling_candidates_without_embeddings(self) -> None:
        groups.reload()
        self.assertEqual(groups.related_skills_of("Cloud-Native Infrastructure"), ["Kubernetes", "Azure"])
        employees = [
            {"id": 1, "employee_no": "E1", "name": "Kubernetes engineer", "background": "", "roles": [],
             "domain_tags": [], "skills": {"Kubernetes": {"rating": 4, "category": "Cloud & DevOps"}}},
            {"id": 2, "employee_no": "E2", "name": "Azure engineer", "background": "", "roles": [],
             "domain_tags": [], "skills": {"Azure": {"rating": 3, "category": "Cloud & DevOps"}}},
        ]
        settings = SimpleNamespace(adjacency_min_similarity=0.6)
        project = ProjectInput(requirements=[Requirement(skill="Cloud-Native Infrastructure", level=3)])
        with (
            patch.object(projects, "_note_unknown"),
            patch.object(projects.repo, "all_profiles", return_value=employees),
            patch.object(projects, "get_settings", return_value=settings),
            patch.object(projects, "_skill_vectors", return_value={}),
            patch.object(projects.predict, "predict_risk", return_value=0.1),
        ):
            report = projects.gap_report(project)

        candidates = report["items"][0]["upskill"]
        self.assertEqual([candidate["id"] for candidate in candidates], [1, 2])
        self.assertEqual([candidate["adjacent_skill"] for candidate in candidates], ["Kubernetes", "Azure"])

    def test_database_schema_creates_domain_tag_storage_and_shell_skill(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.executescript(db.SCHEMA)
        self.assertIsNotNone(conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='employee_domain_tags'"
        ).fetchone())
        taxonomy = json.loads(
            (Path(__file__).parents[1] / "app" / "data" / "skill_taxonomy.json").read_text(encoding="utf-8")
        )
        self.assertIn("Shell Scripting", taxonomy)
        self.assertIn("bash scripting", taxonomy["Shell Scripting"]["aliases"])
        self.assertIn("bash", taxonomy["Shell Scripting"]["aliases"])

    def test_existing_taxonomy_moves_bash_alias_from_linux_to_shell_scripting(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE taxonomy (name TEXT PRIMARY KEY, category TEXT, aliases TEXT)")
        conn.execute("CREATE TABLE employee_skills (canonical_skill TEXT, category TEXT)")
        conn.execute("INSERT INTO taxonomy VALUES ('Linux', 'Cloud & DevOps', '[\"unix\", \"bash\"]')")
        conn.execute("INSERT INTO taxonomy VALUES ('PostgreSQL', 'Data', '[]')")
        conn.execute("INSERT INTO taxonomy VALUES ('Custom Skill', 'Custom Category', '[]')")
        conn.execute("INSERT INTO employee_skills VALUES ('PostgreSQL', 'Data')")

        @contextmanager
        def connect():
            yield conn

        with patch.object(standardization.db, "connect", connect):
            standardization._seed_table()

        linux = json.loads(conn.execute("SELECT aliases FROM taxonomy WHERE name='Linux'").fetchone()["aliases"])
        shell = json.loads(conn.execute(
            "SELECT aliases FROM taxonomy WHERE name='Shell Scripting'"
        ).fetchone()["aliases"])
        self.assertEqual(linux, ["unix"])
        self.assertIn("bash", shell)
        self.assertEqual(
            conn.execute("SELECT category FROM taxonomy WHERE name='PostgreSQL'").fetchone()["category"],
            "Databases",
        )
        self.assertEqual(
            conn.execute("SELECT category FROM employee_skills WHERE canonical_skill='PostgreSQL'").fetchone()["category"],
            "Databases",
        )
        self.assertEqual(
            conn.execute("SELECT category FROM taxonomy WHERE name='Custom Skill'").fetchone()["category"],
            "Custom Category",
        )
        self.assertTrue(standardization.consume_category_sync_change())
        self.assertFalse(standardization.consume_category_sync_change())

    def test_taxonomy_vector_metadata_is_updated_when_category_changes(self) -> None:
        taxonomy = {"PostgreSQL": {"category": "Databases", "aliases": []}}
        existing = {
            "PostgreSQL": {
                "id": "PostgreSQL",
                "document": "PostgreSQL",
                "metadata": {"entity_type": "taxonomy_skill", "category": "Data"},
            }
        }
        with (
            patch.object(standardization, "index", return_value=(taxonomy, {})),
            patch.object(standardization.vectorstore, "get_documents", return_value=existing),
            patch.object(standardization.vectorstore, "upsert") as upsert,
        ):
            standardization.ensure_taxonomy_indexed()

        upsert.assert_called_once_with(
            "taxonomy",
            ["PostgreSQL"],
            ["PostgreSQL"],
            [{"entity_type": "taxonomy_skill", "canonical_skill": "PostgreSQL",
              "name": "PostgreSQL", "category": "Databases"}],
        )


if __name__ == "__main__":
    unittest.main()
