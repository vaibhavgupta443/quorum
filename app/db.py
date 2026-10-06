"""A tiny 'workspace' database: team members, tasks, calendar events and email drafts.

This is the fake company system the agent works against. In a real product these tables would be
Jira/Asana, Google Calendar and Gmail, and the MCP server would wrap their APIs instead of SQLite.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .config import ROOT, db_path

SCHEMA = """
CREATE TABLE IF NOT EXISTS people (
    id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, email TEXT NOT NULL, role TEXT NOT NULL, jira_account_id TEXT
);
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY, title TEXT NOT NULL, owner TEXT NOT NULL, due TEXT, priority TEXT NOT NULL DEFAULT 'medium',
    status TEXT NOT NULL DEFAULT 'open', source TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY, title TEXT NOT NULL, start TEXT NOT NULL, end TEXT NOT NULL, attendees TEXT NOT NULL  -- JSON list
);
CREATE TABLE IF NOT EXISTS drafts (
    id INTEGER PRIMARY KEY, to_addrs TEXT NOT NULL, subject TEXT NOT NULL, body TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

PEOPLE = [
    ("Priya Sharma", "priya.sharma@acme.test", "Backend engineer"),
    ("Priya Nair", "priya.nair@acme.test", "Product manager"),  # two Priyas on purpose: ambiguity!
    ("Rahul Mehta", "rahul.mehta@acme.test", "Frontend engineer"),
    ("Ananya Iyer", "ananya.iyer@acme.test", "Designer"),
    ("Karan Verma", "karan.verma@acme.test", "QA engineer"),
]

TASKS = [  # (title, owner, due, priority)
    ("Write API documentation", "Priya Sharma", "2026-10-10", "medium"),
    ("Fix login redirect bug", "Rahul Mehta", "2026-10-09", "high"),
    ("Prepare Q4 roadmap deck", "Priya Nair", "2026-10-14", "medium"),
]

EVENTS = [  # (title, start, end, attendees)
    ("Sprint review", "2026-10-08T15:00", "2026-10-08T16:00", ["Rahul Mehta", "Priya Sharma", "Karan Verma"]),
    ("Design critique", "2026-10-07T11:00", "2026-10-07T12:00", ["Ananya Iyer", "Priya Nair"]),
    ("Release checklist", "2026-10-09T10:00", "2026-10-09T10:30", ["Karan Verma", "Rahul Mehta"]),
]


@contextmanager
def connect(path: Path | str | None = None) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(str(path or db_path()))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def rows(sql: str, params: tuple | list = ()) -> list[dict[str, Any]]:
    with connect() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def init_db(path: Path | str | None = None) -> None:
    with connect(path) as conn:
        conn.executescript(SCHEMA)


def load_team() -> list[tuple[str, str, str, str | None]]:
    """The team directory. If boardbot/team.json exists (see team.example.json) use it, so the agent knows YOUR real
    teammates and their emails; otherwise use the fake demo team."""
    team_file = ROOT / "team.json"
    if team_file.is_file():
        data = json.loads(team_file.read_text(encoding="utf-8"))
        return [(p["name"], p["email"], p.get("role", "Team member"), p.get("jira_account_id")) for p in data]
    return [(n, e, r, None) for n, e, r in PEOPLE]


def seed(path: Path | str | None = None, reset: bool = True) -> None:
    target = Path(path or db_path())
    if reset and target.exists():
        target.unlink()
    init_db(target)
    with connect(target) as conn:
        conn.executemany("INSERT INTO people (name, email, role, jira_account_id) VALUES (?,?,?,?)", load_team())
        conn.executemany("INSERT INTO tasks (title, owner, due, priority, source) VALUES (?,?,?,?, 'seed')", TASKS)
        conn.executemany(
            "INSERT INTO events (title, start, end, attendees) VALUES (?,?,?,?)",
            [(t, s, e, json.dumps(a)) for t, s, e, a in EVENTS],
        )


def ensure_seeded() -> None:
    """Create the database and demo data if it doesn't exist yet (never wipes existing data)."""
    init_db()
    if not rows("SELECT 1 FROM people LIMIT 1"):
        seed(reset=False)
