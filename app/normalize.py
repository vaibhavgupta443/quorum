"""NORMALIZE: code that turns the plan's words into facts, using the SAME MCP tools the agent has plus the date helpers.

    owner_text   --find_person-->   exact team member, or "Which Priya did you mean?" if it is ambiguous
    due_text     --find_date-->     ISO due date (relative to the MEETING date: 'Friday' means the Friday after that meeting)
    when_text    --resolve_when-->  exact start time; moved to a free slot if it clashes (check_conflicts)
    new task     --search_tasks-->  'skip_duplicate' if it already exists

Why: models are good at READING and unreliable at precise work (names, calendar arithmetic, lookups). Each of those has exactly
one right answer, so code does it. Every repair is reported so the UI can show "the code fixed X".
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime

from .config import today
from .dates import find_date, resolve_when
from .models import EmailItem, Plan
from .tools import MCPToolbox

EVERYONE = {"all", "everyone", "everybody", "team", "whole team", "the team"}


async def _json(toolbox: MCPToolbox, tool: str, args: dict) -> dict | None:
    result = await toolbox.call(tool, args)
    return None if result.is_error else json.loads(result.content)


def _valid_iso(value: str | None) -> bool:
    try:
        return bool(value) and bool(datetime.strptime(value, "%Y-%m-%d"))
    except ValueError:
        return False


async def normalize_plan(plan: Plan, toolbox: MCPToolbox, ref: date | None = None) -> list[str]:
    """Fix the plan in place. `ref` is the date that relative words ('Friday') are measured from (the meeting date).
    Returns a short human-readable note for every repair made."""
    ref = ref or today()
    notes: list[str] = []
    team = [p["name"] for p in (await _json(toolbox, "list_team_members", {}))["people"]]

    async def people_matching(text: str) -> list[str]:
        return (await _json(toolbox, "find_person", {"query": text}) or {"matches": []})["matches"]

    for t in plan.tasks:
        # 1. WHO: resolve the name as said
        written = (t.owner_text or t.owner or "").strip()
        matches = await people_matching(written) if written else []
        if len(matches) == 1:
            if t.owner != matches[0]:
                notes.append(f"owner '{written}' -> {matches[0]}")
            t.owner, t.question = matches[0], None
        elif written:
            t.owner = None
            t.question = f"Which {written.split()[0].capitalize()} did you mean: {' or '.join(matches)}?" if matches else f"I couldn't find '{written}' in the team directory. Who should own this?"
            notes.append(f"owner '{written}' is {'ambiguous' if matches else 'unknown'}: asking the human")
        elif t.owner is None and not t.question:
            t.question = "Nobody is named for this task. Who should own it?"

        # 2. WHEN: computed from the words, never from the model's arithmetic
        day = find_date(t.due_text, ref)[0] if t.due_text else None
        if day:
            if t.due != day.isoformat():
                notes.append(f"due {t.due_text!r} -> {day.isoformat()}")
            t.due = day.isoformat()
        elif not _valid_iso(t.due):
            t.due = None  # not a real date: leave it empty rather than guess

        # 3. DUPLICATE?: look for the same open task
        if t.action == "create" and t.owner:
            hits = (await _json(toolbox, "search_tasks", {"query": t.title, "owner": t.owner}) or {"matches": []})["matches"]
            if hits and hits[0]["similarity"] >= 0.75:
                t.action, t.existing_task_id = "skip_duplicate", hits[0]["id"]
                notes.append(f"'{t.title}' already exists as #{hits[0]['id']}: will skip")
        if t.action == "skip_duplicate" and t.existing_task_id is None:
            t.action = "create"

    for e in plan.events:
        # 1. WHO: everyone, or resolve each name
        names: list[str] = []
        for a in e.attendees:
            if a.strip().lower() in EVERYONE:
                names += team
            else:
                matches = await people_matching(a)
                if len(matches) == 1:
                    names.append(matches[0])
                else:
                    plan.warnings.append(f"Event '{e.title}': could not tell who '{a}' is ({'ambiguous: ' + ' / '.join(matches) if matches else 'unknown'}); left out.")
        e.attendees = list(dict.fromkeys(names))  # unique, keep order

        # 2. WHEN: compute the start from the words
        when = resolve_when(e.when_text or "", ref)
        if when:
            e.start = when.isoformat(timespec="minutes")
        try:
            begin = datetime.strptime(e.start, "%Y-%m-%dT%H:%M")
        except ValueError:
            continue  # no usable time: verify_plan will report it

        # 3. CLASH?: move to the nearest free slot and say why
        if e.attendees:
            check = await _json(toolbox, "check_conflicts", {"attendees": e.attendees, "start": e.start, "duration_minutes": e.duration_minutes})
            if check and not check["ok"] and check.get("free_slots_same_day"):
                why = ("clashes with " + ", ".join(c["title"] for c in check["conflicts"])) if check["conflicts"] else "is outside business hours"
                wanted = begin.hour * 60 + begin.minute
                slot = min(check["free_slots_same_day"], key=lambda s: abs(int(s[:2]) * 60 + int(s[3:]) - wanted))  # closest to the original time
                e.start = f"{begin.date().isoformat()}T{slot}"
                e.note = f"Moved from {begin.strftime('%H:%M')} to {slot}: {why}"
                notes.append(f"'{e.title}': {e.note[0].lower()}{e.note[1:]}")

    # 4. A MEETING IS NOT A TASK: a model may list 'Sprint review' both as an event and as a task. Drop the task.
    def words(text: str) -> set[str]:
        return {w for w in re.findall(r"[a-z]+", text.lower()) if len(w) > 2}

    for t in list(plan.tasks):
        for e in plan.events:
            a, b = words(t.title), words(e.title)
            if a and b and len(a & b) / min(len(a), len(b)) >= 0.75:
                plan.tasks.remove(t)
                notes.append(f"'{t.title}' is a meeting, not a task: removed from tasks")
                break

    # 5. EMAIL: written by CODE from the validated plan, never by the model. Free text from the model that goes in front of
    #    people is a prompt-injection channel, so the model's own email text is discarded.
    plan.email = build_recap(plan)
    return notes


def build_recap(plan: Plan) -> EmailItem | None:
    """A plain recap of the validated plan, addressed to the people it involves. None if there is nothing to report."""
    if not plan.tasks and not plan.events:
        return None
    recipients = list(dict.fromkeys([t.owner for t in plan.tasks if t.owner] + [a for e in plan.events for a in e.attendees]))
    if not recipients:
        return None
    lines = ["Hi team,", "", "Here is the recap from the meeting."]
    if plan.tasks:
        lines += ["", "Tasks:"]
        for t in plan.tasks:
            who = t.owner or "owner to be confirmed"
            due = f", due {t.due}" if t.due else ""
            tracked = f" (already tracked as #{t.existing_task_id})" if t.action == "skip_duplicate" else ""
            lines.append(f"- {t.title}: {who}{due}{tracked}")
    if plan.events:
        lines += ["", "Meetings:"]
        for e in plan.events:
            lines.append(f"- {e.title}: {e.start.replace('T', ' ')} with {', '.join(e.attendees) or 'TBD'}" + (f" ({e.note})" if e.note else ""))
    return EmailItem(to=recipients, subject=f"Meeting recap {today().isoformat()}", body="\n".join(lines))
