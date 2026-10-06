"""The web backend (FastAPI): paste a meeting transcript, watch the agent work, review the plan, approve it.

Flow:   POST /api/plan  -> returns a job id immediately
        GET  /api/job/{id}?since=N   <- the browser polls this to show live agent steps
        POST /api/execute            <- after the human approves (and maybe edits) the plan

Run:    python -m app.cli serve     (or: uvicorn app.api:app)
"""

from __future__ import annotations

import asyncio
import time
import uuid
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import backends, db
from .config import Settings, default_mode, today
from .executor import execute_plan
from .llm import LLMUnavailable, make_llm
from .models import Plan
from .planner import Planner
from .tools import MCPToolbox

STATIC = Path(__file__).parent / "static"
SAMPLES = Path(__file__).resolve().parents[1] / "samples"
MAX_TRANSCRIPT_CHARS = 200_000


class PlanRequest(BaseModel):
    transcript: str
    meeting_title: str = ""
    meeting_date: str = ""  # YYYY-MM-DD; 'Friday' and 'next week' are measured from this. Empty = today.
    mode: str = ""  # "fast" (default) or "agent"


class RefineRequest(BaseModel):
    session_id: str
    instruction: str


def create_app(llm: Any | None = None, server: Any | None = None) -> FastAPI:
    """`llm` and `server` can be injected (tests pass a scripted fake model and an in-process MCP server)."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db.ensure_seeded()
        settings = Settings.from_env()
        app.state.settings = settings
        app.state.llm = llm or make_llm(settings)
        app.state.sessions: dict[str, Planner] = {}
        app.state.jobs: dict[str, dict[str, Any]] = {}
        async with MCPToolbox(server) as toolbox:  # one MCP server process for the whole app
            app.state.toolbox = toolbox
            yield

    app = FastAPI(title="Quorum", lifespan=lifespan)

    # ------------------------------------------------------------ helpers

    def start_job(session_id: str, work) -> str:
        """Run `work(planner)` in the background; the browser polls the job for events and the final result."""
        job_id = uuid.uuid4().hex[:10]
        job: dict[str, Any] = {"status": "running", "events": [], "result": None, "error": None, "session_id": session_id}
        app.state.jobs[job_id] = job
        planner: Planner = app.state.sessions[session_id]
        planner.on_event = lambda event: job["events"].append(event)

        async def runner() -> None:
            started = time.time()
            try:
                result = await work(planner)
                job["result"] = {
                    "plan": result.plan.model_dump() if result.plan else None,
                    "text": result.text,
                    "stop_reason": result.stop_reason,
                    "usage": result.usage,
                    "seconds": round(time.time() - started, 1),
                    "mode": planner.mode,
                }
                job["status"] = "done"
            except LLMUnavailable as exc:  # e.g. no internet or a bad key: show the fix in the UI
                job["status"], job["error"] = "error", str(exc)
            except Exception as exc:  # show the problem in the UI instead of a silent hang
                job["status"], job["error"] = "error", f"{type(exc).__name__}: {exc}"

        asyncio.create_task(runner())
        return job_id

    def sample_path(name: str) -> Path:
        path = SAMPLES / f"{name}.txt"
        if not path.is_file() or path.parent != SAMPLES:  # no path tricks like ../../secret
            raise HTTPException(404, "Unknown sample")
        return path

    # ------------------------------------------------------------ routes

    @app.get("/api/state")
    async def state() -> dict[str, Any]:
        model = app.state.settings.model
        problem = await app.state.llm.problem()  # e.g. "No API key found...", or None when the model is ready
        return {**backends.workspace_snapshot(), "today": today().isoformat(), "model": model, "model_ready": problem is None, "model_problem": problem}

    @app.get("/api/samples")
    async def samples() -> list[str]:
        return sorted(p.stem for p in SAMPLES.glob("*.txt"))

    @app.get("/api/sample/{name}")
    async def sample(name: str) -> dict[str, str]:
        return {"name": name, "title": name.replace("_", " "), "transcript": sample_path(name).read_text(encoding="utf-8")}

    @app.post("/api/plan")
    async def plan_from_transcript(req: PlanRequest) -> dict[str, str]:
        if len(req.transcript) > MAX_TRANSCRIPT_CHARS:
            raise HTTPException(413, "Transcript too long (max 200,000 characters)")
        if not req.transcript.strip():
            raise HTTPException(400, "The transcript is empty")
        try:
            meeting_date = date.fromisoformat(req.meeting_date) if req.meeting_date else None
        except ValueError:
            raise HTTPException(400, "meeting_date must look like 2026-10-06") from None
        session_id = uuid.uuid4().hex[:10]
        mode = req.mode if req.mode in {"fast", "agent"} else default_mode()  # fast: seconds. agent: adds a tool-calling loop
        app.state.sessions[session_id] = Planner(app.state.llm, app.state.toolbox, mode=mode)
        job_id = start_job(session_id, lambda p: p.create_plan(req.transcript, req.meeting_title, meeting_date))
        return {"session_id": session_id, "job_id": job_id}

    @app.post("/api/refine")
    async def refine(req: RefineRequest) -> dict[str, str]:
        if req.session_id not in app.state.sessions:
            raise HTTPException(404, "Unknown session")
        return {"job_id": start_job(req.session_id, lambda p: p.refine(req.instruction))}

    @app.get("/api/job/{job_id}")
    async def job(job_id: str, since: int = 0) -> dict[str, Any]:
        j = app.state.jobs.get(job_id)
        if j is None:
            raise HTTPException(404, "Unknown job")
        return {"status": j["status"], "events": j["events"][since:], "next": len(j["events"]), "result": j["result"], "error": j["error"]}

    @app.get("/api/transcript/{session_id}")
    async def transcript(session_id: str) -> dict[str, Any]:
        planner = app.state.sessions.get(session_id)
        if planner is None:
            raise HTTPException(404, "Unknown session")
        return {"utterances": [dict(u.__dict__) for u in planner.utterances], "removed": planner.removed}

    @app.post("/api/execute")
    async def execute(plan: Plan) -> dict[str, Any]:
        """Called after the human approves. `plan` may have been edited in the browser; the MCP server re-validates it."""
        results = await execute_plan(app.state.toolbox, plan)
        return {"results": results, "state": backends.workspace_snapshot()}

    @app.post("/api/reset")
    async def reset() -> dict[str, Any]:
        db.seed()
        return backends.workspace_snapshot()

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    return app


app = create_app()
