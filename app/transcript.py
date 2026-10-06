"""Reading a meeting transcript: text in, a numbered list of 'who said what' out.

Supported shapes (mix freely):
    Rahul: I'll do it by Friday.
    [00:01:23] Rahul: I'll do it by Friday.
    00:01:23 Rahul Mehta: I'll do it by Friday.
    Rahul (00:01:23): I'll do it by Friday.
A line with no speaker is treated as a continuation of the previous person's sentence. WebVTT/SRT cue numbers and
'00:00:01.000 --> 00:00:04.000' timing lines are skipped.

Every utterance gets a NUMBER (#1, #2 ...). The rest of the app refers to evidence by that number, which is how a plan item can
say "Priya took this over at #14" and how you can check it in one glance.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

TIME = r"\d{1,2}:\d{2}(?::\d{2})?"
SPEAKER_LINE = re.compile(
    rf"""^\s*
    (?:\[?(?P<t1>{TIME})\]?\s+)?                                  # optional leading timestamp
    (?P<speaker>[A-Z][\w.'\-]*(?:\s+[A-Z][\w.'\-]*){{0,3}})       # a name: 1-4 capitalised words ('Priya S', 'Priya Nair')
    \s*(?:\((?P<t2>{TIME})\))?                                    # optional (timestamp) after the name
    \s*:\s*(?P<text>\S.*)$""",
    re.VERBOSE,
)
SKIP = re.compile(r"^\s*(WEBVTT.*|\d+|.*-->.*|NOTE\b.*)\s*$")


@dataclass
class Utterance:
    index: int  # 1-based; the number everything else refers to
    speaker: str
    time: str | None
    text: str


def parse_transcript(raw: str) -> list[Utterance]:
    utterances: list[Utterance] = []
    for line in raw.splitlines():
        if not line.strip() or SKIP.match(line):
            continue
        m = SPEAKER_LINE.match(line)
        if m:
            utterances.append(Utterance(len(utterances) + 1, m["speaker"].strip(), m["t1"] or m["t2"], m["text"].strip()))
        elif utterances:  # a wrapped line: it continues what the previous person was saying
            utterances[-1].text += " " + line.strip()
        else:
            utterances.append(Utterance(1, "Unknown", None, line.strip()))
    return utterances


def format_for_model(utterances: list[Utterance], meeting_date: date, title: str = "") -> str:
    """The numbered transcript the model reads. The date matters: 'Friday' and 'next week' are relative to the meeting."""
    head = f"MEETING{f': {title}' if title else ''}\nMEETING DATE: {meeting_date.strftime('%A')} {meeting_date.isoformat()}\n\nTRANSCRIPT:"
    lines = [f"#{u.index} {u.speaker}{f' [{u.time}]' if u.time else ''}: {u.text}" for u in utterances]
    return head + "\n" + "\n".join(lines)
