import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app import db
from app.agents import proposals


class ApprovedBriefTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / "test.sqlite"
        self.settings = SimpleNamespace(db_path=self.db_path)
        self.settings_patch = patch("app.db.get_settings", return_value=self.settings)
        self.settings_patch.start()
        db.init_db()

    def tearDown(self) -> None:
        self.settings_patch.stop()
        self.tempdir.cleanup()

    def add_brief(self, title: str = "Data platform") -> int:
        with db.connect() as conn:
            return conn.execute(
                "INSERT INTO project_briefs(title, prompt, requirements, roles, approved_by) VALUES (?,?,?,?,?)",
                (title, "Build a data platform", json.dumps([{"skill": "Python", "level": 3}]), "[]", "pm"),
            ).lastrowid

    def test_counts_waiting_proposals_and_active_approved_briefs(self) -> None:
        brief_id = self.add_brief()
        with db.connect() as conn:
            conn.execute(
                "INSERT INTO agent_proposals(agent, kind, title, payload, status) "
                "VALUES ('test', 'skill_mapping', 'Python', '{}', 'pending')"
            )
            conn.execute(
                "INSERT INTO project_briefs(title, status) VALUES ('Cancelled project', 'cancelled')"
            )

        self.assertEqual(proposals.approval_counts(), {"waiting": 1, "approved_briefs": 1})
        self.assertEqual([brief["id"] for brief in proposals.list_briefs()], [brief_id])

    def test_cancellation_requires_reason_and_records_actor_reason_and_audit(self) -> None:
        brief_id = self.add_brief()
        user = {"id": 7, "username": "hr-user", "role": "hr_manager"}

        with self.assertRaisesRegex(ValueError, "reason is required"):
            proposals.cancel_brief(brief_id, user, "  ")

        with patch.object(proposals.auth, "audit") as audit:
            result = proposals.cancel_brief(brief_id, user, "  Project no longer funded.  ")

        self.assertEqual(result, {"status": "cancelled", "brief_id": brief_id})
        brief = db.one("SELECT status, cancelled_by, cancelled_at, cancellation_reason FROM project_briefs WHERE id=?", (brief_id,))
        self.assertEqual(brief["status"], "cancelled")
        self.assertEqual(brief["cancelled_by"], "hr-user")
        self.assertIsNotNone(brief["cancelled_at"])
        self.assertEqual(brief["cancellation_reason"], "Project no longer funded.")
        audit.assert_called_once_with(
            user,
            "project_brief_cancelled",
            {"brief_id": brief_id, "title": "Data platform", "reason": "Project no longer funded."},
        )
        self.assertEqual(proposals.approval_counts()["approved_briefs"], 0)
        self.assertEqual(proposals.list_briefs(), [])

    def test_cannot_cancel_missing_or_already_cancelled_brief(self) -> None:
        user = {"id": 7, "username": "hr-user", "role": "hr_manager"}
        with self.assertRaisesRegex(LookupError, "not found"):
            proposals.cancel_brief(999, user, "Reason")

        brief_id = self.add_brief()
        with patch.object(proposals.auth, "audit"):
            proposals.cancel_brief(brief_id, user, "Reason")
        with self.assertRaisesRegex(ValueError, "no longer approved"):
            proposals.cancel_brief(brief_id, user, "Another reason")

    def test_approved_brief_standardizes_requirements_and_records_new_skills(self) -> None:
        payload = {
            "title": "Platform upgrade",
            "prompt": "Upgrade platform tooling",
            "requirements": [
                {"skill": "py", "level": 3, "weight": 1.0, "min_people": 1, "domain_tags": []},
                {"skill": "Python", "level": 4, "weight": 1.5, "min_people": 2, "domain_tags": ["Data"]},
                {"skill": "Quantum Weaving", "level": 2, "weight": 1.0, "min_people": 1, "domain_tags": []},
            ],
            "headcount": 3,
            "roles": ["Engineer"],
            "notes": "",
        }
        mapping = {
            "py": {"canonical": "Python", "method": "llm"},
            "Python": {"canonical": "Python", "method": "exact"},
            "Quantum Weaving": {"canonical": "Quantum Weaving", "method": "new"},
        }

        with (
            patch.object(proposals.standardization, "normalize_many", return_value=mapping) as normalize_many,
            patch.object(proposals.requested, "record") as record,
        ):
            result = proposals._apply_brief(payload, {"username": "pm"})

        saved = db.one("SELECT requirements FROM project_briefs WHERE id=?", (result["brief_id"],))
        requirements = json.loads(saved["requirements"])
        self.assertEqual(normalize_many.call_args.args[0], ["py", "Python", "Quantum Weaving"])
        self.assertEqual(
            requirements,
            [
                {"skill": "Python", "level": 4, "weight": 1.5, "min_people": 2, "domain_tags": ["Data"]},
                {"skill": "Quantum Weaving", "level": 2, "weight": 1.0, "min_people": 1, "domain_tags": []},
            ],
        )
        record.assert_called_once_with(["Quantum Weaving"], "Approved project: Platform upgrade. Upgrade platform tooling")

    def test_init_db_migrates_existing_project_briefs_table(self) -> None:
        legacy_path = Path(self.tempdir.name) / "legacy.sqlite"
        with sqlite3.connect(legacy_path) as conn:
            conn.execute(
                "CREATE TABLE project_briefs (id INTEGER PRIMARY KEY, title TEXT NOT NULL, prompt TEXT NOT NULL DEFAULT '', "
                "requirements TEXT NOT NULL DEFAULT '[]', headcount INTEGER, roles TEXT NOT NULL DEFAULT '[]', "
                "notes TEXT NOT NULL DEFAULT '', approved_by TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
            )
            conn.execute("INSERT INTO project_briefs(title) VALUES ('Old approved project')")
        with patch("app.db.get_settings", return_value=SimpleNamespace(db_path=legacy_path)):
            db.init_db()
            migrated = db.one("SELECT status, cancellation_reason FROM project_briefs WHERE title='Old approved project'")

        self.assertEqual(migrated, {"status": "approved", "cancellation_reason": ""})


if __name__ == "__main__":
    unittest.main()
