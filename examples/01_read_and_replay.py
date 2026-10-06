"""EXAMPLE 1: read a transcript and REPLAY a log of what was said. No AI, no key, no internet. Runs instantly.

Run:  python examples/01_read_and_replay.py

This is the idea that makes the whole project work. In a real meeting people change their minds:
    #1  "Karan, can you write the API docs?"          -> owner Karan
    #3  "Actually, Priya S, take the docs instead."   -> owner Priya S     (the later statement wins)

Asking a model to track all of that and write only the final answer is where mistakes happen. So we split the job:
    the MODEL  writes down WHAT WAS SAID, step by step     (you will see its real output on Day 2)
    the CODE   replays those steps in order                 (plain Python: "the latest statement wins")

Below, the "log" is written by hand, exactly the way the model would write it. Change it and re-run to see the plan change.
"""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # so `import app` works from here

from app.models import ExtractionLog, Item, Step
from app.reduce import reduce_log
from app.transcript import parse_transcript

TRANSCRIPT = """\
[00:00:10] Priya Nair: Karan, can you write the API docs?
[00:00:20] Karan: I'm buried with testing.
[00:00:30] Priya Nair: Okay, Priya S, take the API docs instead. Monday works?
[00:00:40] Priya Sharma: Yes, Monday.
[00:01:00] Priya Nair: Let's have a demo sync Thursday at 3pm with Rahul and Ananya.
[00:01:10] Ananya: Thursday doesn't work. Friday at 11?
[00:01:20] Priya Nair: Fine, Friday at 11am.
[00:01:40] Priya Nair: And forget the settings page redo, that was just an idea.
"""

# 1. READ: turn the text into numbered utterances (who said what)
utterances = parse_transcript(TRANSCRIPT)
for u in utterances:
    print(f"#{u.index} {u.speaker:<14} {u.text}")

# 2. WHAT WAS SAID: this is what the AI model produces (a list of steps, in order). Written by hand here.
log = ExtractionLog(
    summary="Docs and a demo sync.",
    items=[Item(id="T1", kind="task", title="Write API docs"), Item(id="M1", kind="meeting", title="Demo sync")],
    steps=[
        Step(utterance=1, item="T1", action="set", who="Karan"),         # overrides Karan
        Step(utterance=4, item="T1", action="set", when_text="Monday"),
        Step(utterance=5, item="M1", action="set", when_text="Thursday at 3pm", attendees=["Rahul", "Ananya"]),
        Step(utterance=6, item="M1", action="set", when_text="Friday at 11"),   # moves the day; the meeting item keeps its attendees
        Step(utterance=7, item="M1", action="set", when_text="Friday at 11am"),
        Step(utterance=8, item="T1", action="cancel"),

    ],
)

# 3. REPLAY: plain code applies the steps in transcript order. The meeting was on Tuesday 2026-10-06.
plan = reduce_log(log, utterances, meeting_date=date(2026, 10, 6))

print("\nFINAL STATE (after replaying the steps):")
for t in plan.tasks:
    print(f"  task    {t.title}: owner={t.owner_text}, due={t.due}")
    for h in t.history:
        print(f"            history: {h}")
for e in plan.events:
    print(f"  meeting {e.title}: {e.start}")
    for h in e.history:
        print(f"            history: {h}")

# Try it: (a) delete the Step at utterance 3 -> the owner stays Karan. (b) add Step(utterance=8, item="T1", action="cancel")
# -> the task disappears from the plan and shows up in the notes. (c) move the meeting to "next Tuesday at 2pm".
