"""Jira and Google Calendar backends, tested WITHOUT touching the real services:
Jira through a mocked HTTP transport, Google through a fake service object. Then end to end through the MCP tools."""

import asyncio
import json

import httpx
import pytest
from mcp import Client

from app import backends, db
from app.backends import BackendError, GoogleCalendar, JiraBoard
from app.mcp_server import mcp


@pytest.fixture(autouse=True)
def _reset_overrides():
    yield
    backends.set_overrides()


def jira_with(handler) -> JiraBoard:
    return JiraBoard("https://acme.atlassian.net", "me@acme.test", "token", "KAN", transport=httpx.MockTransport(handler))


def issue(key, summary, assignee=None, due=None, labels=()):
    return {"key": key, "fields": {"summary": summary, "assignee": {"displayName": assignee} if assignee else None, "duedate": due,
                                   "priority": {"name": "High"}, "status": {"name": "To Do"}, "labels": list(labels)}}


# ------------------------------------------------------------------ Jira


def test_jira_list_open_maps_issues_and_uses_current_search_endpoint():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"], seen["body"] = request.url.path, json.loads(request.content)
        seen["auth"] = request.headers["authorization"].startswith("Basic ")
        return httpx.Response(200, json={"issues": [issue("KAN-1", "Write API documentation", "Priya Sharma", "2026-10-10"),
                                                    issue("KAN-2", "Fix login", None, None, ["boardbot", "owner-Rahul_Mehta"])]})

    tasks = jira_with(handler).list_open()
    assert seen["path"] == "/rest/api/3/search/jql" and seen["auth"]
    assert 'project = "KAN"' in seen["body"]["jql"] and "statusCategory != Done" in seen["body"]["jql"]
    assert tasks[0] == {"id": "KAN-1", "title": "Write API documentation", "owner": "Priya Sharma", "due": "2026-10-10",
                        "priority": "high", "status": "To Do", "url": "https://acme.atlassian.net/browse/KAN-1"}
    assert tasks[1]["owner"] == "Rahul Mehta"  # recovered from the owner-* label when nobody is assigned


def test_jira_falls_back_to_classic_search_on_404():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(404) if request.url.path.endswith("/jql") else httpx.Response(200, json={"issues": [issue("KAN-3", "x")]})

    assert jira_with(handler).list_open()[0]["id"] == "KAN-3" and calls == ["/rest/api/3/search/jql", "/rest/api/3/search"]


def test_jira_create_builds_the_right_issue_and_assigns():
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/rest/api/3/user/search":
            return httpx.Response(200, json=[{"accountId": "acc-123"}])
        bodies.append(json.loads(request.content))
        return httpx.Response(201, json={"key": "KAN-7"})

    made = jira_with(handler).create("Refactor auth", {"name": "Rahul Mehta", "email": "rahul@acme.test"}, "2026-10-09", "high", "Rahul - auth - Fri")
    f = bodies[0]["fields"]
    assert made == {"id": "KAN-7", "url": "https://acme.atlassian.net/browse/KAN-7", "assigned": True, "dropped_fields": []}
    assert f["project"] == {"key": "KAN"} and f["summary"] == "Refactor auth" and f["duedate"] == "2026-10-09"
    assert f["priority"] == {"name": "High"} and f["assignee"] == {"accountId": "acc-123"}
    assert "owner-Rahul_Mehta" in f["labels"] and f["description"]["type"] == "doc"  # description must be Atlassian Document Format


def test_jira_create_drops_fields_the_project_does_not_allow():
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/rest/api/3/user/search":
            return httpx.Response(200, json=[])  # no Jira account for this person
        bodies.append(json.loads(request.content)["fields"])
        if "priority" in bodies[-1]:
            return httpx.Response(400, json={"errors": {"priority": "Field 'priority' cannot be set. It is not on the appropriate screen."}})
        return httpx.Response(201, json={"key": "KAN-8"})

    made = jira_with(handler).create("Docs", {"name": "Ananya Iyer", "email": "a@acme.test"}, None, "low", "x")
    assert made["dropped_fields"] == ["priority"] and made["assigned"] is False
    assert "priority" not in bodies[-1] and "assignee" not in bodies[-1] and "duedate" not in bodies[-1]


def test_jira_errors_are_explained():
    with pytest.raises(BackendError, match="BOARDBOT_JIRA_EMAIL"):
        jira_with(lambda r: httpx.Response(401)).list_open()
    with pytest.raises(BackendError, match="project key 'KAN'"):
        jira_with(lambda r: httpx.Response(404)).create("x", {"name": "A", "email": "a@x"}, None, "low", "")


def test_jira_check():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"displayName": "Vaibhav"} if request.url.path.endswith("myself") else {"name": "Kanban demo"})

    assert jira_with(handler).check() == "Jira OK: signed in as Vaibhav, project KAN (Kanban demo)"


# ------------------------------------------------------------------ Google Calendar


class _Run:
    def __init__(self, value):
        self.value = value

    def execute(self):
        return self.value


class FakeGoogle:
    """Just enough of the Google Calendar client for our code: events().list/insert and calendars().get."""

    def __init__(self, items, owner="rahul.mehta@acme.test"):
        self.items, self.owner, self.inserted, self.list_kwargs = items, owner, [], None

    def events(self):
        return self

    def calendars(self):
        return self

    def list(self, **kw):
        self.list_kwargs = kw
        return _Run({"items": self.items})

    def get(self, **kw):
        return _Run({"id": self.owner})

    def insert(self, **kw):
        self.inserted.append(kw)
        return _Run({"id": "evt-1", "htmlLink": "https://calendar.google.com/event?eid=1"})


def gcal(items) -> tuple[GoogleCalendar, FakeGoogle]:
    fake = FakeGoogle(items)
    return GoogleCalendar("c.json", "t.json", "Asia/Kolkata", service=fake), fake


def gev(id, title, start, end, attendees=None, **extra):
    ev = {"id": id, "summary": title, "start": {"dateTime": start}, "end": {"dateTime": end}, **extra}
    if attendees is not None:
        ev["attendees"] = attendees
    return ev


def test_google_events_are_mapped_to_local_time_and_team_names(workspace):
    from datetime import datetime

    cal, fake = gcal([
        gev("1", "Sprint review", "2026-10-08T09:30:00Z", "2026-10-08T10:30:00Z",
            [{"email": "priya.sharma@acme.test"}, {"email": "outsider@else.com"}]),  # 09:30 UTC = 15:00 in India
        gev("2", "Focus time", "2026-10-08T11:00:00+05:30", "2026-10-08T12:00:00+05:30"),  # no attendees: busy for the calendar owner
        gev("3", "Declined", "2026-10-08T13:00:00+05:30", "2026-10-08T14:00:00+05:30", [{"email": "rahul.mehta@acme.test", "self": True, "responseStatus": "declined"}]),
        {"id": "4", "summary": "Holiday", "start": {"date": "2026-10-08"}, "end": {"date": "2026-10-09"}},  # all-day: ignored
        gev("5", "Cancelled", "2026-10-08T16:00:00+05:30", "2026-10-08T17:00:00+05:30", status="cancelled"),
    ])
    events = cal.events_between(datetime(2026, 10, 8), datetime(2026, 10, 9), db.rows("SELECT * FROM people"))
    assert [(e["title"], e["start"], e["attendees"]) for e in events] == [
        ("Sprint review", "2026-10-08T15:00", ["Priya Sharma"]),
        ("Focus time", "2026-10-08T11:00", ["Rahul Mehta"]),
    ]
    assert fake.list_kwargs["singleEvents"] is True and fake.list_kwargs["timeMin"].endswith("+05:30")


def test_google_create_never_emails_attendees(workspace):
    from datetime import datetime

    cal, fake = gcal([])
    people = [p for p in db.rows("SELECT * FROM people") if p["name"] in ("Rahul Mehta", "Ananya Iyer")]
    made = cal.create("Demo prep sync", people, datetime(2026, 10, 8, 16, 0), datetime(2026, 10, 8, 16, 30))
    call = fake.inserted[0]
    assert made == {"id": "evt-1", "url": "https://calendar.google.com/event?eid=1"}
    assert call["sendUpdates"] == "none"  # safe for demos with real addresses
    assert call["body"]["start"] == {"dateTime": "2026-10-08T16:00:00", "timeZone": "Asia/Kolkata"}
    assert [a["email"] for a in call["body"]["attendees"]] == ["rahul.mehta@acme.test", "ananya.iyer@acme.test"]


def test_google_not_connected_message(tmp_path):
    from datetime import datetime

    cal = GoogleCalendar(str(tmp_path / "creds.json"), str(tmp_path / "missing_token.json"), "Asia/Kolkata")
    with pytest.raises(BackendError, match="connect google"):
        cal.events_between(datetime(2026, 10, 8), datetime(2026, 10, 9), [])


# ------------------------------------------------------------------ end to end through the MCP tools


def call_tool(name: str, args: dict, expect_error: bool = False):
    async def run():
        async with Client(mcp) as client:
            return await client.call_tool(name, args)

    result = asyncio.run(run())
    assert result.is_error == expect_error, result.content
    text = result.content[0].text
    return text if expect_error else json.loads(text)


def test_mcp_tools_work_unchanged_on_jira_and_google(workspace):
    created = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/rest/api/3/search/jql":
            return httpx.Response(200, json={"issues": [issue("KAN-1", "Write API documentation", "Priya Sharma", "2026-10-10")]})
        if request.url.path == "/rest/api/3/user/search":
            return httpx.Response(200, json=[{"accountId": "acc-1"}])
        created.append(json.loads(request.content)["fields"]["summary"])
        return httpx.Response(201, json={"key": "KAN-9"})

    cal, fake = gcal([gev("1", "Sprint review", "2026-10-08T15:00:00+05:30", "2026-10-08T16:00:00+05:30", [{"email": "rahul.mehta@acme.test"}])])
    backends.set_overrides(tasks=jira_with(handler), calendar=cal)

    # reads: duplicate detection against Jira, conflict detection against Google
    hit = call_tool("search_tasks", {"query": "write API docs"})["matches"][0]
    assert hit["id"] == "KAN-1" and hit["similarity"] >= 0.75
    clash = call_tool("check_conflicts", {"attendees": ["Rahul Mehta", "Ananya Iyer"], "start": "2026-10-08T15:00"})
    assert clash["ok"] is False and clash["conflicts"][0]["title"] == "Sprint review"
    assert "16:00" in clash["free_slots_same_day"] and "15:00" not in clash["free_slots_same_day"]

    # writes: guardrails still apply, then real calls go out
    dup = call_tool("create_task", {"title": "Write API docs", "owner": "Priya Sharma", "due": "2026-10-10"}, expect_error=True)
    assert "KAN-1" in dup and created == []
    ok = call_tool("create_task", {"title": "Refactor auth middleware", "owner": "Rahul Mehta", "due": "2026-10-09"})
    assert ok["task_id"] == "KAN-9" and ok["url"].endswith("/browse/KAN-9") and created == ["Refactor auth middleware"]
    assert "Cannot schedule" in call_tool("create_event", {"title": "Demo", "attendees": ["Rahul Mehta"], "start": "2026-10-08T15:00"}, expect_error=True)
    ev = call_tool("create_event", {"title": "Demo", "attendees": ["Rahul Mehta", "Ananya Iyer"], "start": "2026-10-08T16:00"})
    assert ev["url"].startswith("https://calendar.google.com") and fake.inserted[0]["body"]["summary"] == "Demo"


def test_backend_failures_become_readable_tool_errors(workspace):
    class Broken:
        name = "broken"

        def list_open(self):
            raise BackendError("Jira rejected the login. Check BOARDBOT_JIRA_EMAIL")

    backends.set_overrides(tasks=Broken())
    assert "Jira rejected the login" in call_tool("search_tasks", {"query": "x"}, expect_error=True)


def test_snapshot_reports_backends_and_problems(workspace):
    class Broken:
        name = "google"

        def events_between(self, *a):
            raise BackendError("Google Calendar is not connected yet.")

    backends.set_overrides(calendar=Broken())
    snap = backends.workspace_snapshot()
    assert snap["backends"] == {"tasks": "local", "calendar": "google"}
    assert snap["problems"] == ["Google Calendar is not connected yet."] and len(snap["tasks"]) == 3


def test_missing_jira_settings_are_listed(monkeypatch):
    monkeypatch.setenv("BOARDBOT_TASKS", "jira")
    for k in ("BASE_URL", "EMAIL", "TOKEN", "PROJECT"):
        monkeypatch.delenv(f"BOARDBOT_JIRA_{k}", raising=False)
    with pytest.raises(BackendError, match="BOARDBOT_JIRA_BASE_URL, BOARDBOT_JIRA_EMAIL, BOARDBOT_JIRA_TOKEN, BOARDBOT_JIRA_PROJECT"):
        backends.tasks_backend()


def test_team_json_replaces_the_demo_team(tmp_path, monkeypatch):
    (tmp_path / "team.json").write_text(json.dumps([{"name": "Vaibhav Gupta", "email": "v@gmail.com", "role": "SDE", "jira_account_id": "acc-9"}]), encoding="utf-8")
    monkeypatch.setattr(db, "ROOT", tmp_path)
    monkeypatch.setenv("BOARDBOT_DB", str(tmp_path / "x.db"))
    db.seed()
    assert db.rows("SELECT name, jira_account_id FROM people") == [{"name": "Vaibhav Gupta", "jira_account_id": "acc-9"}]
