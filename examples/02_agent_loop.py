"""EXAMPLE 2: the smallest possible AGENT, using a free hosted model. Two tools plus a loop. Read every line.

Run:  python examples/02_agent_loop.py
Needs a free API key in .env (see SETUP.md).

The model cannot run code. It can only ASK us to run a tool. We run it and show it the result. Repeat until it stops asking.
"""

import asyncio
import json
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings
from app.llm import make_llm


# --- 1. Tools are ordinary Python functions ---------------------------------------------------------------------------
def add_days(start: str, days: int) -> str:
    return (date.fromisoformat(start) + timedelta(days=days)).isoformat()


def weekday_of(day: str) -> str:
    return date.fromisoformat(day).strftime("%A")


# --- 2. The model only sees a NAME, a DESCRIPTION and a JSON SCHEMA for each tool -------------------------------------
TOOLS = [
    {
        "name": "add_days",
        "description": "Add a number of days to an ISO date (YYYY-MM-DD) and return the new ISO date.",
        "input_schema": {"type": "object", "properties": {"start": {"type": "string"}, "days": {"type": "integer"}}, "required": ["start", "days"]},
    },
    {
        "name": "weekday_of",
        "description": "Return the weekday name of an ISO date.",
        "input_schema": {"type": "object", "properties": {"day": {"type": "string"}}, "required": ["day"]},
    },
]
RUNNERS = {"add_days": add_days, "weekday_of": weekday_of}


async def main() -> None:
    s = Settings.from_env()
    llm = make_llm(s)

    # --- 3. The loop -------------------------------------------------------------------------------------------------
    messages = [
        {"role": "system", "content": "You are a careful assistant. Use the tools for date maths; never calculate dates yourself."},
        {"role": "user", "content": "Today is 2026-10-06. What weekday is it 45 days from now?"},
    ]
    for round_number in range(1, 8):  # a hard limit, so a confused agent can never loop forever
        reply = await llm.chat(messages, tools=TOOLS)
        assistant = {"role": "assistant", "content": reply.text}
        if reply.tool_calls:
            assistant["tool_calls"] = [{"id": c.id, "function": {"name": c.name, "arguments": c.arguments}} for c in reply.tool_calls]
        messages.append(assistant)  # the model is stateless: we keep the history and resend it each round

        if not reply.tool_calls:  # no tool requested -> this is the final answer
            print("\nFINAL ANSWER:", reply.text)
            return

        for c in reply.tool_calls:
            output = RUNNERS[c.name](**c.arguments)
            print(f"round {round_number}: model asked for {c.name}({json.dumps(c.arguments)}) -> {output}")
            messages.append({"role": "tool", "tool_name": c.name, "tool_call_id": c.id, "content": output})  # the id links result to call


asyncio.run(main())

# Experiments: remove the `weekday_of` tool and see what the model does. Add a bug to add_days; does it notice?
# Try a smaller model (BOARDBOT_MODEL=openai/gpt-oss-20b) and see whether it still calls tools correctly.
# The real version of this loop, with more safety rails, is app/agent.py.
