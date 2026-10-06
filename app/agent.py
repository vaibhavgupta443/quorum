"""THE AGENT LOOP. If you understand this file, you understand agentic AI.

    messages = [user: the task]
    repeat:
        reply = ask the model (it can see messages + the list of tools)
        if the reply asks for tools:   -> run them, add the results to messages, go round again
        else:                          -> the model is done, stop

The model never executes anything. It only *asks* for a tool call (a name + JSON arguments).
Our code runs the tool and shows the result to the model on the next round.

Smaller models make more mistakes than big ones, so the loop has extra safety rails (all in code):
    max_turns        a stuck agent can't loop forever
    token_budget     a runaway agent can't fill the model's memory
    repeat guard     calling the same tool with the same arguments twice just returns the earlier answer
    arg check        missing required arguments become an error message the model can read and fix
    truncation       a giant tool result can't flood the context
    errors as data   a failing tool becomes a message the model can react to, not a crash
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, Callable

from .llm import LLM, ToolCall
from .tools import Tool, ToolResult, preview

MAX_RESULT_CHARS = 6_000  # a huge tool result would flood the model's context

EventHandler = Callable[[dict[str, Any]], None]


@dataclass
class RunResult:
    text: str  # the model's last text message
    stop_reason: str  # end_turn | max_turns | token_budget
    turns: int
    usage: dict[str, int]
    trace: list[dict[str, Any]]  # one entry per tool call: what was asked, what came back
    messages: list[dict[str, Any]] = field(repr=False, default_factory=list)  # history WITHOUT the system message


class Agent:
    def __init__(
        self,
        llm: LLM,
        system: str,
        tools: list[Tool],
        on_event: EventHandler | None = None,
        max_turns: int = 12,
        token_budget: int = 120_000,
    ):
        self.llm, self.system = llm, system
        self.tools = {t.name: t for t in tools}
        self.on_event = on_event or (lambda event: None)
        self.max_turns, self.token_budget = max_turns, token_budget

    async def run(self, user_text: str, history: list[dict[str, Any]] | None = None) -> RunResult:
        """Pass an earlier RunResult.messages as `history` to continue that conversation."""
        messages = list(history or []) + [{"role": "user", "content": user_text}]
        trace: list[dict[str, Any]] = []
        seen: dict[str, str] = {}  # (tool, args) -> result, for the repeat guard
        usage = {"input_tokens": 0, "output_tokens": 0}
        text, stop, turns = "", "max_turns", 0
        tool_specs = [t.to_api() for t in self.tools.values()]

        for turns in range(1, self.max_turns + 1):
            # The model is stateless: we send the system prompt + the whole conversation every time.
            reply = await self.llm.chat([{"role": "system", "content": self.system}] + messages, tools=tool_specs)
            usage["input_tokens"] += reply.input_tokens
            usage["output_tokens"] += reply.output_tokens

            assistant: dict[str, Any] = {"role": "assistant", "content": reply.text}
            if reply.tool_calls:
                assistant["tool_calls"] = [{"id": c.id, "function": {"name": c.name, "arguments": c.arguments}} for c in reply.tool_calls]
            messages.append(assistant)
            text = reply.text or text

            if not reply.tool_calls:  # no tool requested -> the model considers itself done
                stop = "end_turn"
                break

            # Run every requested tool (in parallel); each result becomes one 'tool' message.
            outcomes = await asyncio.gather(*(self._run_tool(c, turns, seen) for c in reply.tool_calls))
            for call, content, entry in outcomes:
                messages.append({"role": "tool", "tool_name": call.name, "tool_call_id": call.id, "content": content})
                trace.append(entry)

            if usage["input_tokens"] > self.token_budget:
                stop = "token_budget"
                break

        return RunResult(text, stop, turns, usage, trace, messages)

    # ---------------------------------------------------------------- internals

    async def _run_tool(self, call: ToolCall, turn: int, seen: dict[str, str]) -> tuple[ToolCall, str, dict[str, Any]]:
        self.on_event({"type": "tool_call", "tool": call.name, "input": call.arguments, "turn": turn})
        tool = self.tools.get(call.name)
        if tool is not None:
            call.arguments = _fix_argument_names(tool, call.arguments)
        key = call.name + json.dumps(call.arguments, sort_keys=True)

        if tool is None:  # the model invented a tool name: tell it what exists
            result = ToolResult(f"Unknown tool {call.name!r}. Available tools: {sorted(self.tools)}", True)
        elif missing := [k for k in tool.input_schema.get("required", []) if k not in call.arguments]:
            result = ToolResult(f"Missing required argument(s) {missing} for {call.name}. Expected: {json.dumps(tool.input_schema.get('properties', {}))}", True)
        elif key in seen:
            result = ToolResult(f"(You already made this exact call. Same result as before:)\n{seen[key]}")
        else:
            try:
                result = await tool.call(call.arguments)
            except Exception as exc:  # a bug in a tool must not kill the run; the model sees the error instead
                result = ToolResult(f"Tool crashed: {type(exc).__name__}: {exc}", True)
            if not result.is_error:
                seen[key] = result.content

        content = result.content
        if len(content) > MAX_RESULT_CHARS:
            content = content[:MAX_RESULT_CHARS] + "\n...[truncated; narrow your request]"

        entry = {"tool": call.name, "input": call.arguments, "is_error": result.is_error, "preview": preview(content)}
        self.on_event({"type": "tool_result", "turn": turn, **entry})
        return call, content, entry


def _fix_argument_names(tool: Tool, args: dict[str, Any]) -> dict[str, Any]:
    """Small models often get ONE argument name wrong (search_tasks(text=...) instead of query=...). If exactly one required
    argument is missing and exactly one unknown argument was given, the intent is obvious: rename it. Be liberal in what
    you accept; it saves whole rounds of 'missing argument' errors."""
    allowed = tool.input_schema.get("properties", {})
    missing = [k for k in tool.input_schema.get("required", []) if k not in args]
    unknown = [k for k in args if k not in allowed]
    if len(missing) == 1 and len(unknown) == 1:
        fixed = {k: v for k, v in args.items() if k != unknown[0]}
        fixed[missing[0]] = args[unknown[0]]
        return fixed
    return args
