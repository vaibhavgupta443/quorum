"""Talk to the MCP server the way a real client does (in-process, no subprocess)."""

import asyncio
import json

from mcp import Client

from app import db
from app.mcp_server import mcp


def call(name: str, args: dict | None = None, expect_error: bool = False):
    async def run():
        async with Client(mcp) as client:
            return await client.call_tool(name, args or {})

    result = asyncio.run(run())
    assert result.is_error == expect_error, result.content
    text = result.content[0].text
    return text if expect_error else json.loads(text)


def test_tools_are_discoverable_and_annotated(workspace):
    async def run():
        async with Client(mcp) as client:
            return {t.name: t for t in (await client.list_tools()).tools}

    tools = asyncio.run(run())
    assert {"find_person", "resolve_date", "search_tasks", "check_conflicts", "create_task", "create_event"} <= set(tools)
    assert tools["find_person"].annotations.read_only_hint is True
    assert tools["create_task"].annotations.read_only_hint is False


def test_find_person_handles_ambiguity(workspace):
    assert call("find_person", {"query": "Rahul"})["matches"] == ["Rahul Mehta"]
    ambiguous = call("find_person", {"query": "Priya"})
    assert ambiguous["ambiguous"] and len(ambiguous["matches"]) == 2
    assert call("find_person", {"query": "Priya N."})["matches"] == ["Priya Nair"]
    assert call("find_person", {"query": "Zed"})["found"] is False


def test_resolve_date_tool(workspace):
    out = call("resolve_date", {"phrase": "next Wed"})
    assert out["date"] == "2026-10-14" and out["weekday"] == "Wednesday"
    assert call("resolve_date", {"phrase": "Friday", "relative_to": "2026-10-13"})["date"] == "2026-10-16"  # measured from the MEETING date


def test_search_tasks_finds_near_duplicate(workspace):
    hits = call("search_tasks", {"query": "write API docs"})["matches"]
    assert hits and hits[0]["id"] == 1 and hits[0]["similarity"] >= 0.75
    assert call("search_tasks", {"query": "buy snacks"})["matches"] == []


def test_conflict_detection_and_free_slots(workspace):
    # Rahul has "Sprint review" Thu 15:00-16:00
    out = call("check_conflicts", {"attendees": ["Rahul Mehta", "Ananya Iyer"], "start": "2026-10-08T15:00", "duration_minutes": 30})
    assert out["ok"] is False
    assert out["conflicts"][0]["title"] == "Sprint review" and out["conflicts"][0]["people_busy"] == ["Rahul Mehta"]
    assert "15:00" not in out["free_slots_same_day"] and "16:00" in out["free_slots_same_day"]


def test_outside_business_hours_and_weekend(workspace):
    assert call("check_conflicts", {"attendees": ["Karan Verma"], "start": "2026-10-08T20:00"})["outside_business_hours"]
    assert call("check_conflicts", {"attendees": ["Karan Verma"], "start": "2026-10-10T10:00"})["outside_business_hours"]  # Saturday


def test_create_task_happy_path(workspace):
    out = call("create_task", {"title": "Refactor auth middleware", "owner": "Rahul Mehta", "due": "2026-10-09", "priority": "high"})
    assert out["ok"]
    assert db.rows("SELECT owner, due FROM tasks WHERE id = ?", (out["task_id"],)) == [{"owner": "Rahul Mehta", "due": "2026-10-09"}]


def test_create_task_guardrails(workspace):
    assert "Unknown people" in call("create_task", {"title": "x", "owner": "Priya"}, expect_error=True)
    assert "in the past" in call("create_task", {"title": "x", "owner": "Rahul Mehta", "due": "2026-10-01"}, expect_error=True)
    assert "Duplicate of existing task #1" in call(
        "create_task", {"title": "Write API docs", "owner": "Priya Sharma", "due": "2026-10-10"}, expect_error=True
    )


def test_create_event_rejects_clash_and_suggests_slots(workspace):
    err = call("create_event", {"title": "Demo prep", "attendees": ["Rahul Mehta", "Ananya Iyer"], "start": "2026-10-08T15:00"}, expect_error=True)
    assert "Sprint review" in err and "free_slots_same_day" in err
    ok = call("create_event", {"title": "Demo prep", "attendees": ["Rahul Mehta", "Ananya Iyer"], "start": "2026-10-08T16:00"})
    assert ok["ok"]
    # now the slot is taken for them
    assert call("check_conflicts", {"attendees": ["Ananya Iyer"], "start": "2026-10-08T16:00"})["ok"] is False


def test_email_draft_is_saved_not_sent(workspace):
    out = call("create_email_draft", {"to": ["Rahul Mehta"], "subject": "Notes", "body": "Hi"})
    assert out["to"] == ["rahul.mehta@acme.test"]
    assert len(db.rows("SELECT * FROM drafts")) == 1


def test_resource_and_prompt(workspace):
    async def run():
        async with Client(mcp) as client:
            team = await client.read_resource("workspace://team")
            prompt = await client.get_prompt("plan_from_transcript", {"meeting_date": "2026-10-06"})
            return team, prompt

    team, prompt = asyncio.run(run())
    assert "Priya Nair" in team.contents[0].text
    assert "relative_to=2026-10-06" in prompt.messages[0].content.text and "find_person" in prompt.messages[0].content.text
