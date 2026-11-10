import base64
import json
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException

from app import db, deps
from app.agents import proposals
from app.services import auth

CHROME_MAC = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
SAFARI_IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"
EDGE_WINDOWS = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36 Edg/130.0"


class _TempDb(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        settings = SimpleNamespace(db_path=Path(self.tempdir.name) / "test.sqlite", session_hours=12, auth_secret="test-secret-" + "x" * 32)
        self.patches = [patch("app.db.get_settings", return_value=settings), patch("app.services.auth.get_settings", return_value=settings)]
        for p in self.patches:
            p.start()
        db.init_db()
        self.admin = auth.create_user("admin", "admin-password-1", "admin")
        self.pm = auth.create_user("pm", "pm-password-123", "project_manager")

    def tearDown(self) -> None:
        for p in self.patches:
            p.stop()
        self.tempdir.cleanup()


class OneSessionPerUserTests(_TempDb):
    def test_new_login_ends_the_previous_session(self) -> None:
        first = auth.start_session(self.pm["id"], CHROME_MAC)
        second = auth.start_session(self.pm["id"], SAFARI_IPHONE)
        self.assertEqual(auth.session_from_token(first), (None, "replaced"))
        user, reason = auth.session_from_token(second)
        self.assertEqual((user["username"], reason), ("pm", ""))
        self.assertEqual(db.one("SELECT COUNT(*) AS n FROM user_sessions WHERE user_id=? AND ended_at IS NULL", (self.pm["id"],))["n"], 1)
        self.assertTrue(db.one("SELECT 1 AS x FROM audit_log WHERE action='session_replaced'"))

    def test_other_users_sessions_are_not_touched(self) -> None:
        pm_token = auth.start_session(self.pm["id"], CHROME_MAC)
        auth.start_session(self.admin["id"], CHROME_MAC)
        self.assertIsNotNone(auth.session_from_token(pm_token)[0])

    def test_database_refuses_a_second_open_session(self) -> None:
        auth.start_session(self.pm["id"])
        with self.assertRaises(sqlite3.IntegrityError), db.connect() as c:
            c.execute("INSERT INTO user_sessions(id, user_id, expires_at) VALUES ('x', ?, datetime('now', '+1 hours'))", (self.pm["id"],))

    def test_logout_ends_the_session_on_the_server(self) -> None:
        token = auth.start_session(self.pm["id"])
        user, _ = auth.session_from_token(token)
        auth.end_session(user["session_id"], "logout")
        self.assertEqual(auth.session_from_token(token), (None, "logout"))

    def test_admin_can_end_a_session(self) -> None:
        token = auth.start_session(self.pm["id"], CHROME_MAC)
        [row] = auth.open_sessions()
        self.assertEqual((row["username"], row["device"]), ("pm", "Chrome on macOS"))
        self.assertEqual(auth.end_session(row["id"], "ended_by_admin")["username"], "pm")
        self.assertEqual(auth.session_from_token(token), (None, "ended_by_admin"))
        self.assertEqual(auth.open_sessions(), [])
        self.assertIsNone(auth.end_session(row["id"], "ended_by_admin"))  # already ended

    def test_password_change_ends_the_session(self) -> None:
        token = auth.start_session(self.pm["id"])
        auth.update_user(self.pm["id"], self.admin, password="a-new-password-1")
        self.assertEqual(auth.session_from_token(token), (None, "account_changed"))

    def test_expired_session(self) -> None:
        token = auth.start_session(self.pm["id"])
        with db.connect() as c:
            c.execute("UPDATE user_sessions SET expires_at=datetime('now', '-1 minutes')")
        self.assertEqual(auth.session_from_token(token), (None, "expired"))
        self.assertEqual(auth.open_sessions(), [])

    def test_cookie_from_before_sessions_were_stored_must_sign_in_again(self) -> None:
        body = base64.urlsafe_b64encode(json.dumps({"uid": self.pm["id"], "v": 0, "exp": int(time.time()) + 3600}).encode()).decode()
        import hashlib
        import hmac
        sig = hmac.new(auth._secret(), body.encode(), hashlib.sha256).hexdigest()
        self.assertEqual(auth.session_from_token(f"{body}.{sig}"), (None, "expired"))

    def test_tampered_or_missing_cookie(self) -> None:
        token = auth.start_session(self.pm["id"])
        self.assertEqual(auth.session_from_token(token[:-2] + "00")[1], "missing")
        self.assertEqual(auth.session_from_token(None)[1], "missing")

    def test_401_tells_the_person_why(self) -> None:
        first = auth.start_session(self.pm["id"])
        auth.start_session(self.pm["id"])
        with self.assertRaises(HTTPException) as e:
            deps.current_user(first)
        self.assertEqual(e.exception.status_code, 401)
        self.assertIn("signed in on another device", e.exception.detail)
        with self.assertRaises(HTTPException) as e:
            deps.current_user(None)
        self.assertEqual(e.exception.detail, "Please log in")

    def test_device_label_never_stores_the_raw_user_agent(self) -> None:
        self.assertEqual(auth.device_label(CHROME_MAC), "Chrome on macOS")
        self.assertEqual(auth.device_label(SAFARI_IPHONE), "Safari on iOS")
        self.assertEqual(auth.device_label(EDGE_WINDOWS), "Edge on Windows")
        self.assertEqual(auth.device_label(""), "Unknown browser on unknown system")


class DecideOnceTests(_TempDb):
    def _proposal(self) -> int:
        with db.connect() as c:
            return c.execute("INSERT INTO agent_proposals(agent, kind, title, payload) VALUES ('test', 'skill_mapping', 'Map it', '{}')").lastrowid

    def test_two_simultaneous_approvals_apply_once(self) -> None:
        pid, applied, errors = self._proposal(), [], []

        def slow_apply(payload, user):
            applied.append(user["username"])
            time.sleep(0.3)
            return {}

        def approve():
            try:
                proposals.decide(pid, self.admin, "approve")
            except ValueError as e:
                errors.append(str(e))

        with patch.dict(proposals._APPLY, {"skill_mapping": slow_apply}), patch.object(proposals, "_validate", side_effect=lambda k, p: p):
            threads = [threading.Thread(target=approve) for _ in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        self.assertEqual(len(applied), 1)
        self.assertEqual(len(errors), 1)
        self.assertIn("already decided", errors[0])
        self.assertEqual(db.one("SELECT status FROM agent_proposals WHERE id=?", (pid,))["status"], "approved")

    def test_failed_apply_returns_the_proposal_to_the_queue(self) -> None:
        pid = self._proposal()
        with patch.dict(proposals._APPLY, {"skill_mapping": lambda payload, user: 1 / 0}):
            with self.assertRaises(ZeroDivisionError):
                proposals.decide(pid, self.admin, "approve")
        self.assertEqual(db.one("SELECT status FROM agent_proposals WHERE id=?", (pid,))["status"], "pending")

    def test_restart_releases_interrupted_decisions(self) -> None:
        pid = self._proposal()
        with db.connect() as c:
            c.execute("UPDATE agent_proposals SET status='deciding' WHERE id=?", (pid,))
        self.assertEqual(proposals.release_interrupted_decisions(), 1)
        self.assertEqual(db.one("SELECT status FROM agent_proposals WHERE id=?", (pid,))["status"], "pending")


if __name__ == "__main__":
    unittest.main()
