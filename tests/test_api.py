"""The whole web flow against a fake model: transcript -> live job -> plan -> approve -> workspace changes."""

import time

from fastapi.testclient import TestClient

from app.api import create_app
from app.mcp_server import mcp
from app.models import Plan
from app.planner import plan_for_editing
from tests.fakes import MEETING, FakeLLM, good_log, json_reply


def wait_for(client: TestClient, job_id: str) -> dict:
    for _ in range(200):
        job = client.get(f"/api/job/{job_id}").json()
        if job["status"] != "running":
            return job
        time.sleep(0.05)
    raise AssertionError("job never finished")


def make_client(llm) -> TestClient:
    return TestClient(create_app(llm=llm, server=mcp))


REQUEST = {"transcript": MEETING, "meeting_title": "Sprint sync", "meeting_date": "2026-10-06"}


def test_full_flow(workspace):
    llm = FakeLLM([json_reply(good_log())])
    with make_client(llm) as client:
        state = client.get("/api/state").json()
        assert state["model_ready"] is True and state["backends"] == {"tasks": "local", "calendar": "local"}
        assert "sprint_sync" in client.get("/api/samples").json()
        sample = client.get("/api/sample/sprint_sync").json()
        assert sample["transcript"].startswith("[00:00:05] Priya Nair")

        started = client.post("/api/plan", json=REQUEST).json()
        job = wait_for(client, started["job_id"])
        assert job["status"] == "done", job
        assert job["result"]["mode"] == "fast" and job["result"]["seconds"] >= 0 and len(llm.requests) == 1  # default: ONE model call
        notes = [e for e in job["events"] if e["type"] == "note"]  # the live feed
        assert notes[0]["text"] == "Reading the transcript" and any(e.get("utterances") for e in notes) and any(e.get("log") for e in notes)

        plan = job["result"]["plan"]
        tasks = {t["title"]: t for t in plan["tasks"]}
        assert tasks["Update release notes"]["owner"] is None  # 'Priya' is ambiguous: escalated, not guessed
        assert tasks["Auth refactor"]["history"] and plan["events"][0]["evidence"] == [7, 8]

        shown = client.get(f"/api/transcript/{started['session_id']}").json()
        assert len(shown["utterances"]) == 10 and shown["removed"] == []

        # The human answers the question in the UI, then approves.
        tasks["Update release notes"]["owner"] = "Priya Nair"
        out = client.post("/api/execute", json=plan).json()
        statuses = {(r["kind"], r["title"]): r["status"] for r in out["results"]}
        assert statuses[("task", "Auth refactor")] == "created" and statuses[("task", "Update release notes")] == "created"
        assert statuses[("task", "Write API docs")] == "skipped"
        assert statuses[("event", "Demo prep sync")] == "created" and statuses[("email", "Meeting recap 2026-10-06")] == "drafted"
        assert len(out["state"]["tasks"]) == 5  # 3 seeded + 2 created

        # The server guardrail also protects against a bad hand-edit
        plan["events"][0]["start"] = "2026-10-08T15:00"
        again = {r["kind"]: r["status"] for r in client.post("/api/execute", json=plan).json()["results"]}
        assert again["event"] == "failed"


def test_agent_mode_can_be_requested(workspace):
    from tests.fakes import call, text

    llm = FakeLLM([call(("find_person", {"query": "Rahul"})), text("READY"), json_reply(good_log())])
    with make_client(llm) as client:
        started = client.post("/api/plan", json={**REQUEST, "mode": "agent"}).json()
        job = wait_for(client, started["job_id"])
        assert job["status"] == "done" and job["result"]["mode"] == "agent" and len(llm.requests) == 3
        assert any(e["type"] == "tool_call" and e["tool"] == "find_person" for e in job["events"])


def test_refine_endpoint(workspace):
    llm = FakeLLM([json_reply(good_log())])
    with make_client(llm) as client:
        started = client.post("/api/plan", json=REQUEST).json()
        first = wait_for(client, started["job_id"])
        edited = plan_for_editing(Plan.model_validate(first["result"]["plan"]))
        edited["events"][0]["when_text"] = "Thursday 4pm"  # what the model would write for "move the demo to Thursday at 4pm"
        llm.replies.append(json_reply(edited))
        refined = client.post("/api/refine", json={"session_id": started["session_id"], "instruction": "move the demo to Thursday 4pm"}).json()
        job = wait_for(client, refined["job_id"])
        assert job["result"]["plan"]["events"][0]["start"] == "2026-10-08T16:00"


def test_requests_that_cannot_work_are_refused_clearly(workspace):
    with make_client(FakeLLM([])) as client:
        assert client.post("/api/plan", json={**REQUEST, "transcript": "   "}).status_code == 400
        assert client.post("/api/plan", json={**REQUEST, "meeting_date": "next tuesday"}).status_code == 400
        assert client.post("/api/plan", json={**REQUEST, "transcript": "x" * 200_001}).status_code == 413
        assert client.get("/api/sample/nope").status_code == 404
        assert client.get("/api/sample/..%2F..%2Fsecret").status_code == 404
        assert client.get("/api/job/zzz").status_code == 404
        assert client.get("/api/transcript/zzz").status_code == 404
        assert client.post("/api/refine", json={"session_id": "zzz", "instruction": "x"}).status_code == 404


def test_model_not_ready_is_reported_clearly(workspace):
    with make_client(FakeLLM([], installed=False)) as client:
        state = client.get("/api/state").json()
        assert state["model_ready"] is False and "No API key found" in state["model_problem"]


def test_model_unreachable_shows_a_helpful_job_error(workspace):
    from app.llm import LLMUnavailable

    class Down(FakeLLM):
        async def chat(self, *a, **k):
            raise LLMUnavailable("Could not reach https://api.groq.com. Check your internet connection.")

        async def problem(self):
            return "Could not reach the model API. Check your internet connection."

    with make_client(Down([])) as client:
        assert "Could not reach the model API" in client.get("/api/state").json()["model_problem"]
        job = wait_for(client, client.post("/api/plan", json=REQUEST).json()["job_id"])
        assert job["status"] == "error" and "Could not reach" in job["error"]


def test_reset_restores_demo_data(workspace):
    with make_client(FakeLLM([])) as client:
        plan = {"summary": "x", "tasks": [{"title": "Extra", "owner": "Rahul Mehta"}], "events": [], "warnings": []}
        client.post("/api/execute", json=plan)
        assert len(client.get("/api/state").json()["tasks"]) == 4
        assert len(client.post("/api/reset").json()["tasks"]) == 3


def test_page_is_served(workspace):
    with make_client(FakeLLM([])) as client:
        assert "Quorum" in client.get("/").text
