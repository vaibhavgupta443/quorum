"""EXAMPLE 4: getting a model to answer in exact JSON (structured output).

Run:  python examples/04_structured_output.py
Needs a free API key in .env (see SETUP.md).

Asking a model "please reply in JSON" works most of the time. We turn on JSON mode, put the JSON SCHEMA in the prompt,
and validate the answer with Pydantic (retrying with the error if it fails). That is how app/planner.py gets a Plan.

NOTE what is guaranteed and what is not: the SHAPE is guaranteed, the CONTENT is not (it can still be wrong).
That is why the planner also VERIFIES the content with code afterwards.
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pydantic import BaseModel

from app.config import Settings
from app.llm import make_llm


class ActionItem(BaseModel):
    owner: str
    task: str
    due_text: str


class Notes(BaseModel):
    items: list[ActionItem]


async def main() -> None:
    s = Settings.from_env()
    llm = make_llm(s)
    text = "Rahul - refactor auth middleware - by Fri. Ananya - new onboarding mockups - next Wed."
    reply = await llm.chat(
        [{"role": "user", "content": f"List the action items in these notes:\n{text}"}],
        schema=Notes.model_json_schema(),
    )
    notes = Notes.model_validate_json(reply.text)  # Pydantic double-checks the shape
    print(json.dumps(notes.model_dump(), indent=2))


asyncio.run(main())
