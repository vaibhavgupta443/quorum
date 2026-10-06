"""REDUCE: replay what happened in the meeting, in order, to get the FINAL state of every task and meeting.

A transcript is a story with changes of mind:

    #3  Priya N: Karan, can you write the API docs?        -> owner Karan
    #5  Karan:   I'm buried with testing.
    #6  Priya N: Then Priya S, take the API docs instead.  -> owner Priya S          (the earlier owner is overridden)
    #9  Priya N: Let's do the demo sync Thursday 3pm.      -> meeting Thu 15:00
    #11 Ananya:  Thursday doesn't work, how about Friday?  -> meeting day becomes Friday, the time (15:00) is kept

Asking a language model to hold all of that in its head and write only the final answer is where small models slip. So we split
the job:

    the MODEL  lists WHAT WAS SAID, step by step, with utterance numbers      (it is good at reading and coreference: 'that', 'it')
    this file  APPLIES the steps in order with plain code                      (it is perfectly reliable at "latest wins")

Bonus: every change is recorded, so the plan can show "owner Karan -> Priya S (#6)" and you can verify it against the transcript.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from .dates import find_date, find_time
from .models import EventItem, ExtractionLog, Plan, TaskItem
from .transcript import Utterance

SELF = {"i", "me", "myself", "i'll", "ill"}  # 'I'll do it' means the person speaking
EVERYONE = {"all", "everyone", "everybody", "team", "whole team", "the team"}


@dataclass
class _State:
    id: str
    kind: str
    title: str
    owner: str | None = None
    day: date | None = None
    clock: time | None = None
    attendees: list[str] = field(default_factory=list)
    cancelled: bool = False
    firm: bool = True
    history: list[str] = field(default_factory=list)
    evidence: list[int] = field(default_factory=list)
    speakers: list[str] = field(default_factory=list)
    source: str = ""

    @property
    def last_evidence(self) -> int:
        return max(self.evidence, default=0)


def _when(day: date | None, clock: time | None) -> str:
    return " ".join(p for p in (day.isoformat() if day else None, clock.strftime("%H:%M") if clock else None) if p) or "unset"


def _names(text: str, speaker: str) -> list[str]:
    """'Rahul and Ananya' -> ['Rahul', 'Ananya'];  'I' -> the person who said it."""
    parts = [p.strip() for p in re.split(r",|\band\b|&", text) if p.strip()]
    return [speaker if p.lower() in SELF else p for p in parts]


FILLER = {"the", "for", "new", "and", "with", "write", "update", "create", "make", "fix", "review", "prepare", "check", "send", "run", "finish", "start", "build", "complete"}


def _words(text: str) -> set[str]:
    """The words that identify WHICH task or meeting this is (generic verbs and filler don't)."""
    return {w for w in re.findall(r"[a-z]+", text.lower()) if len(w) > 2 and w not in FILLER}


def _similar(a: str, b: str) -> bool:
    x, y = _words(a), _words(b)
    return bool(x and y) and len(x & y) / min(len(x), len(y)) >= 0.75


def reduce_log(log: ExtractionLog, utterances: list[Utterance], meeting_date: date) -> Plan:
    """Turn the model's step-by-step log into the final Plan. `meeting_date` is what 'Friday' and 'next week' are relative to."""
    by_number = {u.index: u for u in utterances}
    states = {item.id: _State(item.id, item.kind, item.title.strip()) for item in log.items}
    warnings = list(log.warnings)

    # Apply the steps in TRANSCRIPT order (not the order the model happened to list them): later words override earlier words.
    for _, step in sorted(enumerate(log.steps), key=lambda pair: (pair[1].utterance, pair[0])):
        st, u = states.get(step.item), by_number.get(step.utterance)
        if st is None or u is None:
            warnings.append(f"Ignored one extracted step that points at something that does not exist (item {step.item!r}, utterance #{step.utterance}).")
            continue
        tag = f"#{u.index}"
        st.evidence.append(u.index)
        st.speakers.append(u.speaker)
        st.source = u.text

        if step.action == "cancel":
            st.cancelled = True
            st.history.append(f"{tag}: dropped")
            continue
        if step.action == "reinstate":
            st.cancelled = False
            st.history.append(f"{tag}: back on")
            continue

        # action == "set": apply only the fields this utterance actually gives
        if step.who:
            who = _names(step.who, u.speaker)
            if st.kind == "task" and who:
                if len(who) > 1:
                    warnings.append(f"'{st.title}' was given to several people ({', '.join(who)}) at {tag}; using the first. Split it into separate tasks if needed.")
                if st.owner and st.owner.lower() != who[0].lower():
                    st.history.append(f"{tag}: owner {st.owner} -> {who[0]}")
                elif not st.owner:
                    st.history.append(f"{tag}: assigned to {who[0]}")
                st.owner = who[0]
            elif st.kind == "meeting":
                st.attendees = list(dict.fromkeys(st.attendees + who))
        if step.attendees:
            st.attendees = [u.speaker if a.strip().lower() in SELF else a for a in step.attendees]
        if step.when_text:
            day, why = find_date(step.when_text, meeting_date)
            clock = find_time(step.when_text)
            if day and st.day and why.startswith("the next "):
                # A bare weekday ('Wednesday') said AFTER the item already has a day means the same week as that day: moving a
                # Monday meeting to 'Tuesday' then 'Wednesday' means that week's Tuesday and Wednesday, not the coming ones.
                day = st.day - timedelta(days=st.day.weekday()) + timedelta(days=day.weekday())
            if day is None and clock is None:
                warnings.append(f"Could not read the date/time '{step.when_text}' at {tag}; kept the earlier one.")
            else:
                before = _when(st.day, st.clock)
                st.day = day or st.day  # 'Friday' changes the day and keeps the time; '11am' changes the time and keeps the day
                st.clock = clock or st.clock if st.kind == "meeting" else None
                after = _when(st.day, st.clock)
                if before == "unset":
                    st.history.append(f"{tag}: {'due' if st.kind == 'task' else 'scheduled'} {after}")
                elif before != after:
                    st.history.append(f"{tag}: {'due date' if st.kind == 'task' else 'moved'} {before} -> {after}")
        st.firm = step.firm  # the most recent word on the item decides whether it is firm

    live = _merge_mentions([s for s in states.values() if not s.cancelled], warnings)
    tasks: list[TaskItem] = []
    events: list[EventItem] = []

    for st in states.values():
        if st.cancelled:
            warnings.append(f"Dropped in the meeting, so NOT created: '{st.title}' ({'; '.join(st.history)})")
    for st in live:
        evidence = sorted(set(st.evidence))
        if st.kind == "task":
            if not st.firm and not st.owner:
                warnings.append(f"Idea mentioned but nobody committed, so NOT created: '{st.title}' (#{', #'.join(map(str, evidence))})")
                continue
            tasks.append(TaskItem(title=st.title, owner_text=st.owner, due=st.day.isoformat() if st.day else None,
                                  source_text=st.source, history=st.history, evidence=evidence, tentative=not st.firm))
        else:
            if not (st.day and st.clock):
                warnings.append(f"Meeting '{st.title}' has no clear day and time, so it was NOT scheduled (#{', #'.join(map(str, evidence))}).")
                continue
            attendees = st.attendees
            if not attendees:
                attendees = list(dict.fromkeys(st.speakers))
                warnings.append(f"Nobody was named for meeting '{st.title}'; assumed the people who discussed it: {', '.join(attendees)}.")
            events.append(EventItem(title=st.title, attendees=attendees, start=datetime.combine(st.day, st.clock).isoformat(timespec="minutes"),
                                    source_text=st.source, history=st.history, evidence=evidence, tentative=not st.firm))
    return Plan(summary=log.summary, tasks=tasks, events=events, warnings=warnings)


def _merge_mentions(states: list[_State], warnings: list[str]) -> list[_State]:
    """If the model made two items for the same thing (discussed twice, in different words), keep ONE: the later mention wins."""
    kept: list[_State] = []
    for st in states:
        twin = next((k for k in kept if k.kind == st.kind and _similar(k.title, st.title)
                     and (st.kind == "meeting" or not (k.owner and st.owner) or k.owner.lower() == st.owner.lower())), None)
        if twin is None:
            kept.append(st)
            continue
        newer, older = (st, twin) if st.last_evidence >= twin.last_evidence else (twin, st)
        twin.owner = newer.owner or older.owner
        twin.day, twin.clock = newer.day or older.day, newer.clock or older.clock
        twin.attendees = newer.attendees or older.attendees
        twin.firm, twin.source = newer.firm, newer.source
        twin.history = sorted(set(older.history + newer.history), key=lambda h: int(re.match(r"#(\d+)", h).group(1)) if re.match(r"#(\d+)", h) else 0)
        twin.evidence += st.evidence if twin is not st else []
        twin.speakers += st.speakers if twin is not st else []
        warnings.append(f"Merged two mentions of the same {st.kind}: '{twin.title}' (the later mention wins).")
    return kept
