"""The replay step: 'the latest statement wins', with history. Pure code, no model, no database."""

from datetime import date

from app.models import ExtractionLog
from app.reduce import reduce_log
from app.transcript import Utterance, parse_transcript

MEETING_DATE = date(2026, 10, 6)  # a Tuesday


def utterances(*speakers_and_text: tuple[str, str]) -> list[Utterance]:
    return [Utterance(i, s, None, t) for i, (s, t) in enumerate(speakers_and_text, 1)]


def run(items, steps, utts, **kw):
    return reduce_log(ExtractionLog.model_validate({"summary": "s", "items": items, "steps": steps}), utts, kw.get("date", MEETING_DATE))


T = {"id": "T1", "kind": "task", "title": "Write API docs"}
M = {"id": "M1", "kind": "meeting", "title": "Demo sync"}


def test_a_later_owner_overrides_an_earlier_one_and_history_is_kept():
    utts = utterances(("Priya", "Karan, docs?"), ("Karan", "Swamped."), ("Priya", "Then Priya S."))
    plan = run([T], [{"utterance": 1, "item": "T1", "action": "set", "who": "Karan"}, {"utterance": 3, "item": "T1", "action": "set", "who": "Priya S"}], utts)
    task = plan.tasks[0]
    assert task.owner_text == "Priya S" and task.history == ["#1: assigned to Karan", "#3: owner Karan -> Priya S"] and task.evidence == [1, 3]


def test_steps_are_applied_in_transcript_order_not_listed_order():
    utts = utterances(("A", "one"), ("A", "two"))
    steps = [{"utterance": 2, "item": "T1", "action": "set", "who": "Rahul"}, {"utterance": 1, "item": "T1", "action": "set", "who": "Karan"}]  # listed backwards
    assert run([T], steps, utts).tasks[0].owner_text == "Rahul"


def test_i_will_means_the_speaker():
    utts = utterances(("Ananya", "I'll take it."))
    assert run([T], [{"utterance": 1, "item": "T1", "action": "set", "who": "I"}], utts).tasks[0].owner_text == "Ananya"


def test_due_date_changes_are_relative_to_the_meeting_date():
    utts = utterances(("A", "by Friday"), ("A", "no, next Monday"))
    plan = run([T], [{"utterance": 1, "item": "T1", "action": "set", "who": "Rahul", "when_text": "Friday"},
                     {"utterance": 2, "item": "T1", "action": "set", "when_text": "next Monday"}], utts)
    assert plan.tasks[0].due == "2026-10-12" and "#2: due date 2026-10-09 -> 2026-10-12" in plan.tasks[0].history
    later = run([T], [{"utterance": 1, "item": "T1", "action": "set", "who": "Rahul", "when_text": "Friday"}], utts, date=date(2026, 10, 13))
    assert later.tasks[0].due == "2026-10-16"  # a meeting a week later: 'Friday' is a week later too


def test_a_day_change_keeps_the_time_and_a_time_change_keeps_the_day():
    utts = utterances(*[("A", "x")] * 3)
    steps = [{"utterance": 1, "item": "M1", "action": "set", "when_text": "Thursday at 3pm", "attendees": ["Rahul"]},
             {"utterance": 2, "item": "M1", "action": "set", "when_text": "Friday"},
             {"utterance": 3, "item": "M1", "action": "set", "when_text": "11am"}]
    ev = run([M], steps, utts).events[0]
    assert ev.start == "2026-10-09T11:00"
    assert ev.history == ["#1: scheduled 2026-10-08 15:00", "#2: moved 2026-10-08 15:00 -> 2026-10-09 15:00", "#3: moved 2026-10-09 15:00 -> 2026-10-09 11:00"]


def test_a_bare_weekday_after_a_day_is_set_means_the_same_week():
    """Moving a Monday meeting to 'Tuesday' then 'Wednesday' means that week's days, not the next ones coming up."""
    utts = utterances(*[("A", "x")] * 3)
    steps = [{"utterance": 1, "item": "M1", "action": "set", "when_text": "Monday at 10", "attendees": ["Karan"]},
             {"utterance": 2, "item": "M1", "action": "set", "when_text": "Tuesday at 2pm"},
             {"utterance": 3, "item": "M1", "action": "set", "when_text": "Wednesday at 10am"}]
    assert run([M], steps, utts).events[0].start == "2026-10-14T10:00"  # Mon 12th -> Tue 13th -> Wed 14th


def test_cancel_and_reinstate():
    utts = utterances(*[("A", "x")] * 4)
    dropped = run([T], [{"utterance": 1, "item": "T1", "action": "set", "who": "Karan"}, {"utterance": 2, "item": "T1", "action": "cancel"}], utts)
    assert dropped.tasks == [] and any("Dropped in the meeting" in w and "Write API docs" in w for w in dropped.warnings)
    back = run([T], [{"utterance": 1, "item": "T1", "action": "set", "who": "Karan"}, {"utterance": 2, "item": "T1", "action": "cancel"},
                     {"utterance": 3, "item": "T1", "action": "reinstate"}, {"utterance": 4, "item": "T1", "action": "set", "when_text": "next Friday"}], utts)
    assert back.tasks[0].owner_text == "Karan" and back.tasks[0].due == "2026-10-16" and "#3: back on" in back.tasks[0].history


def test_ideas_nobody_committed_to_are_notes_not_tasks():
    utts = utterances(("A", "maybe we should redo settings"))
    plan = run([{"id": "T9", "kind": "task", "title": "Redo settings page"}], [{"utterance": 1, "item": "T9", "action": "set", "firm": False}], utts)
    assert plan.tasks == [] and any("Idea mentioned but nobody committed" in w for w in plan.warnings)
    owned = run([T], [{"utterance": 1, "item": "T1", "action": "set", "who": "Rahul", "firm": False}], utts)
    assert owned.tasks[0].tentative is True  # someone volunteered tentatively: keep it, but flag it


def test_two_items_for_the_same_thing_become_one():
    utts = utterances(*[("A", "x")] * 3)
    items = [T, {"id": "T2", "kind": "task", "title": "API docs for the new endpoints"}]
    steps = [{"utterance": 1, "item": "T1", "action": "set", "who": "Karan"}, {"utterance": 3, "item": "T2", "action": "set", "who": "Karan", "when_text": "Monday"}]
    plan = run(items, steps, utts)
    assert len(plan.tasks) == 1 and plan.tasks[0].due == "2026-10-12" and any("Merged two mentions" in w for w in plan.warnings)


def test_meetings_without_a_clear_time_and_bad_references_are_reported_not_crashed():
    utts = utterances(("A", "x"))
    plan = run([M], [{"utterance": 1, "item": "M1", "action": "set", "when_text": "sometime"}, {"utterance": 99, "item": "M1", "action": "set"},
                     {"utterance": 1, "item": "NOPE", "action": "set"}], utts)
    assert plan.events == []
    assert any("Could not read the date/time 'sometime'" in w for w in plan.warnings)
    assert any("has no clear day and time" in w for w in plan.warnings) and sum("does not exist" in w for w in plan.warnings) == 2


def test_default_attendees_are_the_people_who_discussed_it():
    utts = utterances(("Karan", "I can join"), ("Priya Nair", "Monday at 10"))
    plan = run([M], [{"utterance": 1, "item": "M1", "action": "set"}, {"utterance": 2, "item": "M1", "action": "set", "when_text": "Monday at 10"}], utts)
    assert plan.events[0].attendees == ["Karan", "Priya Nair"] and any("assumed the people who discussed it" in w for w in plan.warnings)


def test_full_example_from_the_parser():
    utts = parse_transcript("Priya: Karan, docs please.\nPriya: No, Rahul takes the docs.")
    plan = run([T], [{"utterance": 1, "item": "T1", "action": "set", "who": "Karan"}, {"utterance": 2, "item": "T1", "action": "set", "who": "Rahul"}], utts)
    assert plan.tasks[0].owner_text == "Rahul"
