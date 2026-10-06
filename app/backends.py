"""Where tasks and calendar events REALLY live. The MCP server talks to these; nothing else in the app does.

    TaskBoard         LocalTasks   (SQLite demo)       JiraBoard     (Jira Cloud REST API v3)
    CalendarBackend   LocalCalendar (SQLite demo)      GoogleCalendar (Google Calendar API v3)

Pick with environment variables (see INTEGRATIONS.md):  BOARDBOT_TASKS=local|jira   BOARDBOT_CALENDAR=local|google

This is the payoff of MCP: the planner agent, the verify step and the UI have no idea which backend is active.
They call the same MCP tools (find_person, search_tasks, check_conflicts, create_task ...). Swapping Jira for Trello
or Google Calendar for Outlook means writing one more class here, and nothing else changes.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, time, timedelta
from typing import Any, Protocol
from zoneinfo import ZoneInfo

import httpx

from . import db
from .config import ROOT, timezone_name, today

Task = dict[str, Any]  # {id, title, owner, due, priority, status, url}
Event = dict[str, Any]  # {id, title, start, end, attendees: [names], url}   (start/end: naive local 'YYYY-MM-DDTHH:MM')


class BackendError(RuntimeError):
    """A problem talking to Jira/Google, worded so a human (or the model) knows what to fix."""


class TaskBoard(Protocol):
    name: str

    def list_open(self) -> list[Task]: ...
    def create(self, title: str, person: dict[str, Any], due: str | None, priority: str, source: str) -> dict[str, Any]: ...
    def check(self) -> str: ...


class CalendarBackend(Protocol):
    name: str

    def events_between(self, start: datetime, end: datetime, people: list[dict[str, Any]]) -> list[Event]: ...
    def create(self, title: str, attendees: list[dict[str, Any]], start: datetime, end: datetime) -> dict[str, Any]: ...
    def check(self) -> str: ...


# ------------------------------------------------------------------ local demo (SQLite)


class LocalTasks:
    name = "local"

    def list_open(self) -> list[Task]:
        return [{**r, "url": None} for r in db.rows("SELECT * FROM tasks WHERE status = 'open' ORDER BY id")]

    def create(self, title, person, due, priority, source) -> dict[str, Any]:
        with db.connect() as conn:
            cur = conn.execute(
                "INSERT INTO tasks (title, owner, due, priority, source) VALUES (?,?,?,?,?)", (title, person["name"], due, priority, source)
            )
        return {"id": cur.lastrowid, "url": None, "assigned": True}

    def check(self) -> str:
        return "local demo database"


class LocalCalendar:
    name = "local"

    def events_between(self, start, end, people) -> list[Event]:
        rows = db.rows("SELECT * FROM events WHERE start < ? AND end > ? ORDER BY start", (end.isoformat(timespec="minutes"), start.isoformat(timespec="minutes")))
        return [{**r, "attendees": json.loads(r["attendees"]), "url": None} for r in rows]

    def create(self, title, attendees, start, end) -> dict[str, Any]:
        with db.connect() as conn:
            cur = conn.execute(
                "INSERT INTO events (title, start, end, attendees) VALUES (?,?,?,?)",
                (title, start.isoformat(timespec="minutes"), end.isoformat(timespec="minutes"), json.dumps([a["name"] for a in attendees])),
            )
        return {"id": cur.lastrowid, "url": None}

    def check(self) -> str:
        return "local demo database"


# ------------------------------------------------------------------ Jira Cloud


def _adf(text: str) -> dict[str, Any]:
    """Jira's description field wants 'Atlassian Document Format' (a JSON tree), not plain text."""
    paragraphs = [{"type": "paragraph", "content": [{"type": "text", "text": line}]} for line in text.splitlines() if line.strip()]
    return {"type": "doc", "version": 1, "content": paragraphs or [{"type": "paragraph", "content": []}]}


class JiraBoard:
    """Jira Cloud (free plan is fine). Auth = your email + an API token (id.atlassian.com/manage-profile/security/api-tokens)."""

    name = "jira"
    PRIORITY = {"low": "Low", "medium": "Medium", "high": "High"}
    OPTIONAL_FIELDS = {"priority", "duedate", "assignee", "labels", "description"}  # dropped (and retried) if a project doesn't allow them

    def __init__(self, base_url: str, email: str, token: str, project: str, issue_type: str = "Task", transport: httpx.BaseTransport | None = None):
        self.base, self.project, self.issue_type = base_url.rstrip("/"), project, issue_type
        self._http = httpx.Client(base_url=self.base, auth=(email, token), headers={"Accept": "application/json"}, timeout=20.0, transport=transport)
        self._accounts: dict[str, str | None] = {}

    # -- helpers
    def _check_response(self, r: httpx.Response) -> None:
        if r.status_code in (401, 403):
            raise BackendError("Jira rejected the login. Check BOARDBOT_JIRA_EMAIL and BOARDBOT_JIRA_TOKEN (an API token, not your password).")
        if r.status_code == 404:
            raise BackendError(f"Jira returned 404. Check BOARDBOT_JIRA_BASE_URL ({self.base}) and the project key '{self.project}'.")
        if r.status_code >= 400:
            raise BackendError(f"Jira error {r.status_code}: {r.text[:300]}")

    def _account_id(self, person: dict[str, Any]) -> str | None:
        """Who to assign to: the id from team.json, or a lookup by email. None = leave unassigned."""
        if person.get("jira_account_id"):
            return person["jira_account_id"]
        email = person.get("email") or ""
        if email not in self._accounts:
            try:
                r = self._http.get("/rest/api/3/user/search", params={"query": email})
                found = r.json() if r.status_code == 200 else []
                self._accounts[email] = found[0]["accountId"] if found else None
            except (httpx.HTTPError, ValueError, KeyError, IndexError):
                self._accounts[email] = None
        return self._accounts[email]

    @staticmethod
    def _slug(name: str) -> str:
        return re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_")

    def _to_task(self, issue: dict[str, Any]) -> Task:
        f = issue.get("fields", {})
        owner = (f.get("assignee") or {}).get("displayName")
        if not owner:
            owner = next((lbl[len("owner-"):].replace("_", " ") for lbl in f.get("labels", []) if lbl.startswith("owner-")), None)
        return {
            "id": issue["key"], "title": f.get("summary", ""), "owner": owner, "due": f.get("duedate"),
            "priority": ((f.get("priority") or {}).get("name") or "medium").lower(),
            "status": (f.get("status") or {}).get("name", "open"), "url": f"{self.base}/browse/{issue['key']}",
        }

    # -- interface
    def list_open(self) -> list[Task]:
        jql = f'project = "{self.project}" AND statusCategory != Done ORDER BY created DESC'
        fields = ["summary", "assignee", "duedate", "priority", "status", "labels"]
        try:
            r = self._http.post("/rest/api/3/search/jql", json={"jql": jql, "maxResults": 100, "fields": fields})
            if r.status_code in (404, 410):  # older Jira sites only have the classic search endpoint
                r = self._http.get("/rest/api/3/search", params={"jql": jql, "maxResults": 100, "fields": ",".join(fields)})
        except httpx.HTTPError as exc:
            raise BackendError(f"Could not reach Jira at {self.base}: {exc}") from exc
        self._check_response(r)
        return [self._to_task(i) for i in r.json().get("issues", [])]

    def create(self, title, person, due, priority, source) -> dict[str, Any]:
        fields: dict[str, Any] = {
            "project": {"key": self.project},
            "summary": title,
            "issuetype": {"name": self.issue_type},
            "labels": ["boardbot", f"owner-{self._slug(person['name'])}"],
            "description": _adf(f"Owner: {person['name']}\nFrom the meeting: {source}\nCreated by Quorum."),
            "priority": {"name": self.PRIORITY.get(priority, "Medium")},
        }
        if due:
            fields["duedate"] = due
        if account := self._account_id(person):
            fields["assignee"] = {"accountId": account}

        dropped: list[str] = []
        for _ in range(5):
            try:
                r = self._http.post("/rest/api/3/issue", json={"fields": fields})
            except httpx.HTTPError as exc:
                raise BackendError(f"Could not reach Jira at {self.base}: {exc}") from exc
            if r.status_code == 201:
                key = r.json()["key"]
                return {"id": key, "url": f"{self.base}/browse/{key}", "assigned": "assignee" in fields, "dropped_fields": dropped}
            if r.status_code == 400:  # e.g. "Field 'priority' cannot be set": remove it and retry
                bad = [k for k in r.json().get("errors", {}) if k in self.OPTIONAL_FIELDS and k in fields]
                if bad:
                    for k in bad:
                        fields.pop(k)
                    dropped += bad
                    continue
            self._check_response(r)
        raise BackendError("Jira kept rejecting the issue after removing optional fields.")

    def check(self) -> str:
        try:
            me = self._http.get("/rest/api/3/myself")
            self._check_response(me)
            proj = self._http.get(f"/rest/api/3/project/{self.project}")
            self._check_response(proj)
        except httpx.HTTPError as exc:
            raise BackendError(f"Could not reach Jira at {self.base}: {exc}") from exc
        return f"Jira OK: signed in as {me.json().get('displayName')}, project {self.project} ({proj.json().get('name')})"


# ------------------------------------------------------------------ Google Calendar

GOOGLE_SCOPES = ["https://www.googleapis.com/auth/calendar"]


def google_connect(credentials_file: str, token_file: str) -> str:
    """One-time browser sign-in. Run via:  python -m app.cli connect google"""
    from google_auth_oauthlib.flow import InstalledAppFlow

    if not os.path.isfile(credentials_file):
        raise BackendError(f"Missing {credentials_file}. See INTEGRATIONS.md step 'Google Calendar' to download it.")
    creds = InstalledAppFlow.from_client_secrets_file(credentials_file, GOOGLE_SCOPES).run_local_server(port=0)
    with open(token_file, "w", encoding="utf-8") as f:
        f.write(creds.to_json())
    return token_file


class GoogleCalendar:
    """Reads and writes ONE Google calendar (the signed-in user's primary). Invites are never emailed (sendUpdates='none'),
    so demoing with real attendee addresses is safe.

    What 'busy' means: Google only shows us the signed-in user's own calendar, so an event is busy time for (a) every team
    member who is an attendee (matched by email) and (b) the calendar owner, if the event has no attendees or they're in it.
    """

    name = "google"

    def __init__(self, credentials_file: str, token_file: str, tz: str, calendar_id: str = "primary", service: Any | None = None):
        self.credentials_file, self.token_file, self.tz, self.calendar_id = credentials_file, token_file, tz, calendar_id
        self._service = service
        self._owner: str | None = None

    def _svc(self) -> Any:
        if self._service is None:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build

            if not os.path.isfile(self.token_file):
                raise BackendError("Google Calendar is not connected yet. Run:  python -m app.cli connect google")
            creds = Credentials.from_authorized_user_file(self.token_file, GOOGLE_SCOPES)
            if not creds.valid and creds.refresh_token:
                creds.refresh(Request())
                with open(self.token_file, "w", encoding="utf-8") as f:
                    f.write(creds.to_json())
            self._service = build("calendar", "v3", credentials=creds, cache_discovery=False)
        return self._service

    def _owner_email(self) -> str:
        if self._owner is None:
            self._owner = self._svc().calendars().get(calendarId=self.calendar_id).execute()["id"]
        return self._owner

    def _local(self, iso: str) -> datetime:
        return datetime.fromisoformat(iso).astimezone(ZoneInfo(self.tz)).replace(tzinfo=None)

    def events_between(self, start, end, people) -> list[Event]:
        tz = ZoneInfo(self.tz)
        try:
            resp = self._svc().events().list(
                calendarId=self.calendar_id, timeMin=start.replace(tzinfo=tz).isoformat(), timeMax=end.replace(tzinfo=tz).isoformat(),
                singleEvents=True, orderBy="startTime", maxResults=250,
            ).execute()
            owner_email = self._owner_email().lower()
        except BackendError:
            raise
        except Exception as exc:  # googleapiclient raises its own HttpError types
            raise BackendError(f"Google Calendar error: {exc}") from exc

        names = {p["email"].lower(): p["name"] for p in people if p.get("email")}
        events: list[Event] = []
        for ev in resp.get("items", []):
            if ev.get("status") == "cancelled" or "dateTime" not in ev.get("start", {}):
                continue  # all-day events don't block meetings
            attendees = ev.get("attendees", [])
            if any(a.get("self") and a.get("responseStatus") == "declined" for a in attendees):
                continue
            who = [names[a["email"].lower()] for a in attendees if a.get("email", "").lower() in names]
            owner_name = names.get(owner_email)
            if owner_name and owner_name not in who and (not attendees or any(a.get("self") for a in attendees)):
                who.append(owner_name)
            events.append({
                "id": ev["id"], "title": ev.get("summary", "(no title)"), "attendees": who, "url": ev.get("htmlLink"),
                "start": self._local(ev["start"]["dateTime"]).isoformat(timespec="minutes"),
                "end": self._local(ev["end"]["dateTime"]).isoformat(timespec="minutes"),
            })
        return events

    def create(self, title, attendees, start, end) -> dict[str, Any]:
        body = {
            "summary": title,
            "description": "Created by Quorum from a meeting transcript.",
            "start": {"dateTime": start.isoformat(timespec="seconds"), "timeZone": self.tz},
            "end": {"dateTime": end.isoformat(timespec="seconds"), "timeZone": self.tz},
            "attendees": [{"email": a["email"]} for a in attendees if a.get("email")],
        }
        try:
            made = self._svc().events().insert(calendarId=self.calendar_id, body=body, sendUpdates="none").execute()
        except BackendError:
            raise
        except Exception as exc:
            raise BackendError(f"Google Calendar error: {exc}") from exc
        return {"id": made["id"], "url": made.get("htmlLink")}

    def check(self) -> str:
        return f"Google Calendar OK: {self._owner_email()} (time zone {self.tz}, invites are NOT emailed)"


# ------------------------------------------------------------------ choosing a backend from settings

_overrides: dict[str, Any] = {}
_cache: dict[tuple, Any] = {}


def set_overrides(tasks: Any | None = None, calendar: Any | None = None) -> None:
    """For tests: force a specific backend object. Call with no arguments to go back to the environment settings."""
    _overrides.clear()
    if tasks is not None:
        _overrides["tasks"] = tasks
    if calendar is not None:
        _overrides["calendar"] = calendar


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def tasks_backend() -> TaskBoard:
    if "tasks" in _overrides:
        return _overrides["tasks"]
    if _env("BOARDBOT_TASKS", "local").lower() == "jira":
        needed = {k: _env(k) for k in ("BOARDBOT_JIRA_BASE_URL", "BOARDBOT_JIRA_EMAIL", "BOARDBOT_JIRA_TOKEN", "BOARDBOT_JIRA_PROJECT")}
        if missing := [k for k, v in needed.items() if not v]:
            raise BackendError(f"BOARDBOT_TASKS=jira but these settings are empty: {', '.join(missing)} (see INTEGRATIONS.md)")
        key = ("jira", *needed.values(), _env("BOARDBOT_JIRA_ISSUE_TYPE", "Task"))
        if key not in _cache:
            v = list(needed.values())
            _cache[key] = JiraBoard(v[0], v[1], v[2], v[3], _env("BOARDBOT_JIRA_ISSUE_TYPE", "Task"))
        return _cache[key]
    return LocalTasks()


def calendar_backend() -> CalendarBackend:
    if "calendar" in _overrides:
        return _overrides["calendar"]
    if _env("BOARDBOT_CALENDAR", "local").lower() == "google":
        creds = _env("BOARDBOT_GOOGLE_CREDENTIALS", str(ROOT / "google_credentials.json"))
        token = _env("BOARDBOT_GOOGLE_TOKEN", str(ROOT / "google_token.json"))
        key = ("google", creds, token, timezone_name())
        if key not in _cache:
            _cache[key] = GoogleCalendar(creds, token, timezone_name())
        return _cache[key]
    return LocalCalendar()


def workspace_snapshot() -> dict[str, Any]:
    """Everything the UI's Workspace panel shows, read from whichever backends are active."""
    people = db.rows("SELECT name, email, role, jira_account_id FROM people ORDER BY id")
    problems: list[str] = []
    tasks: list[Task] = []
    events: list[Event] = []
    names = {}
    try:
        board = tasks_backend()
        names["tasks"] = board.name
        tasks = board.list_open()
    except BackendError as exc:
        problems.append(str(exc))
    try:
        cal = calendar_backend()
        names["calendar"] = cal.name
        start = datetime.combine(today(), time.min)
        events = cal.events_between(start, start + timedelta(days=60), people)
    except BackendError as exc:
        problems.append(str(exc))
    drafts = db.rows("SELECT * FROM drafts ORDER BY id DESC")
    for d in drafts:
        d["to_addrs"] = json.loads(d["to_addrs"])
    return {
        "people": [{k: p[k] for k in ("name", "email", "role")} for p in people],
        "tasks": tasks, "events": events, "drafts": drafts,
        "backends": {"tasks": names.get("tasks", "?"), "calendar": names.get("calendar", "?")}, "problems": problems,
    }
