from datetime import date

import pytest

from app.dates import resolve_date

TODAY = date(2026, 10, 6)  # a Tuesday


@pytest.mark.parametrize(
    "phrase, expected",
    [
        ("fri", date(2026, 10, 9)),
        ("Thurs", date(2026, 10, 8)),
        ("thu", date(2026, 10, 8)),
        ("tue", date(2026, 10, 13)),  # same weekday as today -> the NEXT one
        ("next Wed", date(2026, 10, 14)),
        ("next Tue", date(2026, 10, 13)),
        ("EOW", date(2026, 10, 9)),
        ("tomorrow", date(2026, 10, 7)),
        ("10/14", date(2026, 10, 14)),
        ("Oct 14", date(2026, 10, 14)),
        ("14 oct", date(2026, 10, 14)),
        ("in 2 weeks", date(2026, 10, 20)),
        ("2026-12-01", date(2026, 12, 1)),
        ("EOM", date(2026, 10, 31)),
        ("mon", date(2026, 10, 12)),
        ("9/1", date(2027, 9, 1)),  # already past this year -> next occurrence
        ("end of next week", date(2026, 10, 16)),
        ("next week", date(2026, 10, 12)),
        ("Tuesday next week", date(2026, 10, 13)),
    ],
)
def test_resolve(phrase, expected):
    assert resolve_date(phrase, TODAY)[0] == expected


def test_unparseable():
    assert resolve_date("whenever", TODAY)[0] is None


def test_find_date_and_time_inside_spoken_sentences():
    from app.dates import find_date, find_time, resolve_when

    assert find_date("Okay, I'll have it done by end of next week.", TODAY)[0] == date(2026, 10, 16)
    assert find_date("let's do it Tuesday next week at 11", TODAY)[0] == date(2026, 10, 13)
    assert find_date("it is due on Friday", TODAY)[0] == date(2026, 10, 9)
    assert find_date("we talked about it yesterday", TODAY)[0] is None
    assert (find_time("at 3pm").hour, find_time("at 11").hour, find_time("at 3").hour, find_time("at 12").hour) == (15, 11, 15, 12)  # bare 'at 3' = afternoon
    assert find_time("at 10:30 am").minute == 30 and find_time("no time here") is None
    assert resolve_when("Thursday at 3", TODAY).isoformat() == "2026-10-08T15:00:00"
