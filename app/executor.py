"""The EXECUTOR: turns an APPROVED plan into real workspace changes.

Notice there is no AI in this file. Once a human has approved a plan, carrying it out is a fixed sequence of API
calls, so plain code is the right tool: faster, free, deterministic and easy to test. A good agentic design uses the
model only where judgement is needed (reading the board, resolving ambiguity) and ordinary code for everything else.

The MCP server still validates every write, so even a hand-edited or malformed plan cannot create bad data.
"""

from __future__ import annotations

import json
from typing import Any

from .models import Plan
from .tools import MCPToolbox, ToolResult, preview


def _detail(result: ToolResult) -> str:
    """One line for the UI: the link to the new Jira issue / calendar event if there is one, else the raw message."""
    try:
        data = json.loads(result.content)
    except ValueError:
        return preview(result.content)
    if isinstance(data, dict) and data.get("url"):
        return data["url"] + (f"   ({data['note'].strip()})" if data.get("note") else "")
    if isinstance(data, dict) and data.get("note"):
        return data["note"].strip()
    return preview(result.content)


async def execute_plan(toolbox: MCPToolbox, plan: Plan) -> list[dict[str, Any]]:
    outcomes: list[dict[str, Any]] = []

    def record(kind: str, title: str, status: str, detail: str) -> None:
        outcomes.append({"kind": kind, "title": title, "status": status, "detail": detail})

    for task in plan.tasks:
        if task.action == "skip_duplicate":
            record("task", task.title, "skipped", f"already exists as task #{task.existing_task_id}")
        elif task.owner is None:
            record("task", task.title, "skipped", "no owner chosen yet: " + (task.question or ""))
        else:
            args: dict[str, Any] = {"title": task.title, "owner": task.owner, "priority": task.priority, "source": task.source_text}
            if task.due:
                args["due"] = task.due
            result = await toolbox.call("create_task", args)
            record("task", task.title, "failed" if result.is_error else "created", _detail(result))

    for event in plan.events:
        result = await toolbox.call(
            "create_event",
            {"title": event.title, "attendees": event.attendees, "start": event.start, "duration_minutes": event.duration_minutes},
        )
        record("event", event.title, "failed" if result.is_error else "created", _detail(result))

    if plan.email:
        result = await toolbox.call("create_email_draft", {"to": plan.email.to, "subject": plan.email.subject, "body": plan.email.body})
        record("email", plan.email.subject, "failed" if result.is_error else "drafted", _detail(result))

    return outcomes
