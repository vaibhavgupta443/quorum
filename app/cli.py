"""Command line: the same pipeline as the web app, handy for learning because you see every step.

    python -m app.cli seed
    python -m app.cli doctor                                           # is your API key valid? are Jira/Calendar reachable?
    python -m app.cli plan samples/sprint_sync.txt -v                  # fast mode: a few seconds
    python -m app.cli plan samples/sprint_sync.txt --agent -v          # the slower tool-calling agent loop
    python -m app.cli plan samples/sprint_sync.txt --execute           # asks y/N before creating anything
    python -m app.cli serve                                            # web app on http://127.0.0.1:8000
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import date
from pathlib import Path

from rich.console import Console
from rich.table import Table

from . import backends, db
from .config import ROOT, Settings, db_path, default_mode, today
from .executor import execute_plan
from .llm import LLMUnavailable, make_llm
from .planner import Planner
from .tools import MCPToolbox

console = Console()


def show_event(event: dict) -> None:
    if event["type"] == "note":
        console.print(f"[cyan]  * {event['text']}[/cyan]")
    elif event["type"] == "tool_call":
        console.print(f"[dim]  round {event['turn']}  -> [bold]{event['tool']}[/bold] {json.dumps(event['input'])[:100]}[/dim]")
    elif event["is_error"]:
        console.print(f"[red]     x {event['tool']}: {event['preview'][:120]}[/red]")


def show_plan(plan) -> None:
    console.print(f"\n[bold]{plan.summary}[/bold]")
    table = Table("Task", "Owner", "Due", "Action", "What happened in the meeting", show_lines=True)
    for t in plan.tasks:
        owner = t.owner or f"[yellow]? {t.question}[/yellow]"
        action = t.action if t.action == "create" else f"skip (#{t.existing_task_id})"
        table.add_row(t.title + (" [yellow](tentative)[/yellow]" if t.tentative else ""), owner, t.due or "-", action, "\n".join(t.history) or "-")
    console.print(table)
    for e in plan.events:
        note = f"  [yellow]({e.note})[/yellow]" if e.note else ""
        console.print(f"  Meeting: {e.title}  {e.start}  {', '.join(e.attendees)}{note}")
        for h in e.history:
            console.print(f"      [dim]{h}[/dim]")
    if plan.email:
        console.print(f"  Email draft: {plan.email.subject} -> {', '.join(plan.email.to)}")
    for w in plan.warnings:
        console.print(f"  [yellow]note:[/yellow] {w}")


async def doctor() -> None:
    settings = Settings.from_env()
    problem = await make_llm(settings).problem()
    if problem:
        console.print(f"[red]x[/red] Model ({settings.provider}): {problem}")
    else:
        console.print(f"[green]ok[/green] Model: {settings.provider} / {settings.model} is ready.")

    # Where do tasks and events go? (local demo database, Jira, Google Calendar)
    for label, getter in (("Tasks", backends.tasks_backend), ("Calendar", backends.calendar_backend)):
        try:
            backend = getter()
            console.print(f"[green]ok[/green] {label}: {backend.name} - {await asyncio.to_thread(backend.check)}")
        except backends.BackendError as exc:
            console.print(f"[red]x[/red] {label}: {exc}")


def connect(target: str) -> None:
    """One-time connection helpers for the real services."""
    if target == "google":
        creds = os.environ.get("BOARDBOT_GOOGLE_CREDENTIALS", str(ROOT / "google_credentials.json"))
        token = os.environ.get("BOARDBOT_GOOGLE_TOKEN", str(ROOT / "google_token.json"))
        console.print("A browser window will open: sign in with the Google account whose calendar you want to use.")
        try:
            backends.google_connect(creds, token)
        except backends.BackendError as exc:
            console.print(f"[red]{exc}[/red]")
            return
        console.print(f"[green]Connected.[/green] Saved {token}. Now set BOARDBOT_CALENDAR=google in .env and run doctor.")
    else:
        console.print("Jira needs no sign-in step: put the 5 BOARDBOT_JIRA_* settings in .env (see INTEGRATIONS.md), set BOARDBOT_TASKS=jira, then run:  python -m app.cli doctor")


async def run_plan(args: argparse.Namespace) -> None:
    db.ensure_seeded()
    settings = Settings.from_env()
    transcript = sys.stdin.read() if args.transcript == "-" else Path(args.transcript).read_text(encoding="utf-8")
    meeting_date = date.fromisoformat(args.date) if args.date else None
    async with MCPToolbox() as toolbox:
        planner = Planner(make_llm(settings), toolbox, on_event=show_event if args.verbose else None, mode="agent" if args.agent else default_mode())
        console.print(f"[dim]today = {today()} | model = {settings.model} via {settings.provider} | mode = {planner.mode}[/dim]")
        try:
            result = await planner.create_plan(transcript, args.title, meeting_date)
        except LLMUnavailable as exc:
            console.print(f"[red]{exc}[/red]")
            return
        if result.plan is None:
            console.print(f"[red]No plan produced.[/red] {result.text}")
            return
        show_plan(result.plan)
        u = result.usage
        console.print(f"[dim]{len(result.trace)} tool calls | {u['input_tokens']:,} in / {u['output_tokens']:,} out tokens[/dim]")
        if args.execute and input("\nCreate all of this in the workspace? [y/N] ").strip().lower() == "y":
            for r in await execute_plan(toolbox, result.plan):
                console.print(f"  {r['status']:<8} {r['kind']}: {r['title']}  [dim]{r['detail'][:90]}[/dim]")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="meetingbot")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("seed", help="reset the demo workspace data")
    sub.add_parser("serve", help="run the web app")
    sub.add_parser("doctor", help="check that your API key, Jira and Google Calendar are ready")
    conn = sub.add_parser("connect", help="one-time sign-in for a real service")
    conn.add_argument("target", choices=["google", "jira"])
    plan = sub.add_parser("plan", help="turn a meeting transcript into a plan")
    plan.add_argument("transcript", help="path to a .txt transcript, or - to read from the keyboard/pipe")
    plan.add_argument("--title", default="")
    plan.add_argument("--date", default="", help="meeting date YYYY-MM-DD (days like 'Friday' are measured from it); default: today")
    plan.add_argument("--execute", action="store_true", help="after showing the plan, offer to create everything")
    plan.add_argument("--agent", action="store_true", help="use the slower tool-calling agent loop instead of fast mode")
    plan.add_argument("-v", "--verbose", action="store_true", help="show each step and tool call")
    args = parser.parse_args(argv)

    if args.command == "seed":
        db.seed()
        console.print(f"Seeded {db_path()}")
    elif args.command == "serve":
        import uvicorn

        uvicorn.run("app.api:app", host="127.0.0.1", port=8000)
    elif args.command == "doctor":
        asyncio.run(doctor())
    elif args.command == "connect":
        connect(args.target)
    else:
        try:
            asyncio.run(run_plan(args))
        except KeyboardInterrupt:
            sys.exit(130)


if __name__ == "__main__":
    main()
