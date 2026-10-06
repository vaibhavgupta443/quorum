"""A scripted stand-in for the model, so the agent can be tested instantly with no key, no internet and no cost.

FakeLLM replays the replies you give it, in order, and records every request the agent sent, so tests can check exactly what
the model was shown (messages, tools, schema).
"""

from __future__ import annotations

import json
from typing import Any

from app.llm import Reply, ToolCall


def text(msg: str) -> Reply:
    return Reply(msg, [], 100, 20)


def call(*calls: tuple[str, dict]) -> Reply:
    return Reply("", [ToolCall(n, a) for n, a in calls], 100, 20)


def json_reply(payload: dict) -> Reply:
    """What the model returns when asked for structured JSON."""
    return Reply(json.dumps(payload), [], 200, 150)


class FakeLLM:
    def __init__(self, replies: list[Reply], installed: bool = True):
        self.replies = list(replies)
        self.requests: list[dict[str, Any]] = []
        self.installed = installed

    async def chat(self, messages, tools=None, schema=None) -> Reply:
        # snapshot: the agent keeps appending to the same list after the call returns
        self.requests.append({"messages": list(messages), "tools": tools, "schema": schema})
        assert self.replies, "the fake model ran out of scripted replies"
        return self.replies.pop(0)

    async def problem(self) -> str | None:
        return None if self.installed else "No API key found for groq. Create a free one (see SETUP.md)."


# A short meeting with every kind of complication. Utterance numbers are given by the parser (1-based).
MEETING = """\
[00:00:05] Priya Nair: Rahul, can you take the auth refactor by Friday?
[00:00:10] Rahul: Sure.
[00:00:20] Priya Nair: API docs: Karan, can you write them?
[00:00:30] Karan: I'm swamped.
[00:00:40] Priya Nair: Then Priya S, take the API docs. Monday.
[00:00:50] Priya Nair: Priya, please update the release notes.
[00:01:00] Priya Nair: Demo prep sync Thursday at 3pm with Rahul and Ananya.
[00:01:10] Ananya: Thursday doesn't work. Friday at 11am?
[00:01:20] Priya Nair: Fine, Friday 11am.
[00:01:30] Rahul: Actually make the auth refactor next Monday.
"""


def good_log() -> dict:
    """What a good model returns for MEETING: WHAT WAS SAID, in order (not the final answer)."""
    return {
        "summary": "Sprint planning for the week.",
        "items": [
            {"id": "T1", "kind": "task", "title": "Auth refactor"},
            {"id": "T2", "kind": "task", "title": "Write API docs"},
            {"id": "T3", "kind": "task", "title": "Update release notes"},
            {"id": "M1", "kind": "meeting", "title": "Demo prep sync"},
        ],
        "steps": [
            {"utterance": 1, "item": "T1", "action": "set", "who": "Rahul", "when_text": "Friday"},
            {"utterance": 3, "item": "T2", "action": "set", "who": "Karan"},
            {"utterance": 5, "item": "T2", "action": "set", "who": "Priya S", "when_text": "Monday"},
            {"utterance": 6, "item": "T3", "action": "set", "who": "Priya"},
            {"utterance": 7, "item": "M1", "action": "set", "when_text": "Thursday at 3pm", "attendees": ["Rahul", "Ananya"]},
            {"utterance": 8, "item": "M1", "action": "set", "when_text": "Friday at 11am"},
            {"utterance": 10, "item": "T1", "action": "set", "when_text": "next Monday"},
        ],
        "warnings": [],
    }
