"""SQLite persistence (stdlib sqlite3). Records only; vectors live in Chroma (see ai/vectorstore.py).

    with db.connect() as c: c.execute(...)      # commits on success, rolls back on error, always closes
    db.rows(sql, params) -> list[dict]          db.one(sql, params) -> dict | None
"""
import sqlite3
from contextlib import contextmanager

from .config import get_settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS uploads (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  filename TEXT NOT NULL,
  kind TEXT NOT NULL,
  path TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'parsed',
  settings_json TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS employees (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  employee_no TEXT UNIQUE,
  name TEXT NOT NULL,
  background TEXT NOT NULL DEFAULT '',
  source_upload_id INTEGER,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS employee_roles (
  employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
  role TEXT NOT NULL,
  UNIQUE(employee_id, role)
);
CREATE TABLE IF NOT EXISTS employee_domain_tags (
  employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
  tag TEXT NOT NULL,
  UNIQUE(employee_id, tag)
);
CREATE TABLE IF NOT EXISTS employee_skills (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
  raw_skill TEXT NOT NULL,
  canonical_skill TEXT NOT NULL,
  category TEXT NOT NULL DEFAULT 'Other',
  rating INTEGER,
  method TEXT NOT NULL DEFAULT 'exact',
  confidence REAL NOT NULL DEFAULT 1.0,
  needs_review INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_es_employee ON employee_skills(employee_id);
CREATE INDEX IF NOT EXISTS idx_es_canonical ON employee_skills(canonical_skill);
CREATE TABLE IF NOT EXISTS taxonomy (
  name TEXT PRIMARY KEY,
  category TEXT NOT NULL DEFAULT 'Other',
  aliases TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS mapping_overrides (
  raw_key TEXT PRIMARY KEY,
  canonical TEXT NOT NULL,
  category TEXT
);
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT NOT NULL UNIQUE COLLATE NOCASE,
  display_name TEXT NOT NULL DEFAULT '',
  password_hash TEXT NOT NULL,
  role TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 1,
  token_version INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  user_id INTEGER,
  username TEXT NOT NULL DEFAULT 'system',
  role TEXT NOT NULL DEFAULT '',
  action TEXT NOT NULL,
  detail TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts);
CREATE TABLE IF NOT EXISTS agent_runs (
  id TEXT PRIMARY KEY,
  agent TEXT NOT NULL,
  user_id INTEGER,
  status TEXT NOT NULL DEFAULT 'running',
  input TEXT NOT NULL DEFAULT '{}',
  steps TEXT NOT NULL DEFAULT '[]',
  question TEXT,
  result TEXT,
  error TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  finished_at TEXT
);
CREATE TABLE IF NOT EXISTS agent_proposals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT,
  agent TEXT NOT NULL,
  kind TEXT NOT NULL,
  title TEXT NOT NULL,
  payload TEXT NOT NULL DEFAULT '{}',
  evidence TEXT NOT NULL DEFAULT '',
  confidence REAL,
  status TEXT NOT NULL DEFAULT 'pending',
  decided_by TEXT,
  decided_at TEXT,
  note TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_prop_status ON agent_proposals(status, kind);
CREATE TABLE IF NOT EXISTS project_briefs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  prompt TEXT NOT NULL DEFAULT '',
  requirements TEXT NOT NULL DEFAULT '[]',
  headcount INTEGER,
  roles TEXT NOT NULL DEFAULT '[]',
  notes TEXT NOT NULL DEFAULT '',
  approved_by TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'approved',
  cancelled_by TEXT NOT NULL DEFAULT '',
  cancelled_at TEXT,
  cancellation_reason TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS learning_plans (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
  title TEXT NOT NULL DEFAULT '',
  markdown TEXT NOT NULL,
  requirements TEXT NOT NULL DEFAULT '[]',
  weeks INTEGER,
  hours_per_week INTEGER,
  mode TEXT NOT NULL DEFAULT '',
  proposal_id INTEGER,
  approved_by TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_learning_plans_employee ON learning_plans(employee_id);
CREATE TABLE IF NOT EXISTS project_assignments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  brief_id INTEGER NOT NULL REFERENCES project_briefs(id) ON DELETE CASCADE,
  employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
  position TEXT NOT NULL DEFAULT '',
  allocation_pct INTEGER NOT NULL CHECK (allocation_pct BETWEEN 1 AND 100),
  status TEXT NOT NULL DEFAULT 'active',
  requested_by TEXT NOT NULL DEFAULT '',
  approved_by TEXT NOT NULL DEFAULT '',
  start_date TEXT,
  end_date TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  ended_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_assign_active ON project_assignments(brief_id, employee_id) WHERE status='active';
CREATE INDEX IF NOT EXISTS idx_assign_employee ON project_assignments(employee_id, status);
CREATE TABLE IF NOT EXISTS requested_skills (
  key TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  times_requested INTEGER NOT NULL DEFAULT 1,
  last_context TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'pending',
  first_seen TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_seen TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
-- Login sessions. A user has at most ONE open session (ended_at IS NULL): a new login ends the previous one.
-- device is a short label such as "Chrome on macOS"; the raw user agent and the IP address are not stored. Times are UTC.
CREATE TABLE IF NOT EXISTS user_sessions (
  id TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  device TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  expires_at TEXT NOT NULL,
  ended_at TEXT,
  end_reason TEXT NOT NULL DEFAULT ''
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_one_open_session ON user_sessions(user_id) WHERE ended_at IS NULL;
"""


@contextmanager
def connect():
    conn = sqlite3.connect(get_settings().db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def rows(sql: str, params: tuple = ()) -> list[dict]:
    with connect() as c:
        return [dict(r) for r in c.execute(sql, params).fetchall()]


def one(sql: str, params: tuple = ()) -> dict | None:
    with connect() as c:
        r = c.execute(sql, params).fetchone()
        return dict(r) if r else None


def init_db() -> None:
    with connect() as c:
        c.executescript(SCHEMA)
        columns = {row["name"] for row in c.execute("PRAGMA table_info(project_briefs)")}
        migrations = {
            "status": "TEXT NOT NULL DEFAULT 'approved'",
            "cancelled_by": "TEXT NOT NULL DEFAULT ''",
            "cancelled_at": "TEXT",
            "cancellation_reason": "TEXT NOT NULL DEFAULT ''",
            "start_date": "TEXT",
            "end_date": "TEXT",
        }
        for name, declaration in migrations.items():
            if name not in columns:
                c.execute(f"ALTER TABLE project_briefs ADD COLUMN {name} {declaration}")
        c.execute("CREATE INDEX IF NOT EXISTS idx_project_briefs_status ON project_briefs(status)")
        assign_cols = {row["name"] for row in c.execute("PRAGMA table_info(project_assignments)")}
        for name in ("start_date", "end_date"):  # an assignment may have its own dates inside the project's
            if name not in assign_cols:
                c.execute(f"ALTER TABLE project_assignments ADD COLUMN {name} TEXT")
        # member accounts: the one employee record a member may work on, and the team leader who approves their changes
        user_cols = {row["name"] for row in c.execute("PRAGMA table_info(users)")}
        if "employee_id" not in user_cols:
            c.execute("ALTER TABLE users ADD COLUMN employee_id INTEGER REFERENCES employees(id) ON DELETE SET NULL")
        if "team_leader_id" not in user_cols:
            c.execute("ALTER TABLE users ADD COLUMN team_leader_id INTEGER REFERENCES users(id) ON DELETE SET NULL")
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_employee ON users(employee_id) WHERE employee_id IS NOT NULL")
        # who asked for a proposal (a member's change goes to that member's team leader)
        prop_cols = {row["name"] for row in c.execute("PRAGMA table_info(agent_proposals)")}
        if "requested_by" not in prop_cols:
            c.execute("ALTER TABLE agent_proposals ADD COLUMN requested_by INTEGER")
        c.execute("CREATE INDEX IF NOT EXISTS idx_prop_requested_by ON agent_proposals(requested_by)")
        if "subject_employee_id" not in prop_cols:  # the employee a proposal is ABOUT (role matching: that employee's team leader may decide)
            c.execute("ALTER TABLE agent_proposals ADD COLUMN subject_employee_id INTEGER")
        # who uploaded a file (a member may only submit a CV that they uploaded themselves)
        up_cols = {row["name"] for row in c.execute("PRAGMA table_info(uploads)")}
        if "uploaded_by" not in up_cols:
            c.execute("ALTER TABLE uploads ADD COLUMN uploaded_by INTEGER")
