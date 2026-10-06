"""Turn spoken/short date phrases ('Fri', 'next Wed', 'EOW', '10/14') into real dates.

Why this is a TOOL and not left to the model: models are decent at dates but sometimes off by a
week on 'next Wed'. Anything with one right answer should be computed by code and handed to the model.
"""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta

WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]


WEEKDAY_WORDS = {
    "mon": 0, "monday": 0, "tue": 1, "tues": 1, "tuesday": 1, "wed": 2, "weds": 2, "wednesday": 2,
    "thu": 3, "thur": 3, "thurs": 3, "thursday": 3, "fri": 4, "friday": 4, "sat": 5, "saturday": 5, "sun": 6, "sunday": 6,
}


def _weekday_index(word: str) -> int | None:
    """Only real weekday spellings count, so ordinary words like 'monitor' or 'satellite' are never read as dates."""
    return WEEKDAY_WORDS.get(word.lower().rstrip("."))


def resolve_date(phrase: str, today: date) -> tuple[date | None, str]:
    """Return (date, explanation). date is None if the phrase can't be understood."""
    p = phrase.strip().lower()

    if m := re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", p):
        return date(int(m[1]), int(m[2]), int(m[3])), "ISO date"
    if p in {"today", "eod"}:
        return today, "today"
    if p == "tomorrow":
        return today + timedelta(days=1), "tomorrow"
    if p in {"eow", "end of week", "this week", "by end of week"}:
        friday = today + timedelta(days=(4 - today.weekday()) % 7)
        return friday, "Friday of this week"
    next_monday = today - timedelta(days=today.weekday()) + timedelta(days=7)
    if p in {"end of next week", "by end of next week", "eonw"}:
        return next_monday + timedelta(days=4), "Friday of next week"
    if p == "next week":
        return next_monday, "Monday of next week"
    if m := re.fullmatch(r"([a-z]+) next week|next week ([a-z]+)", p):  # 'Tuesday next week'
        idx = _weekday_index(m[1] or m[2])
        if idx is not None:
            return next_monday + timedelta(days=idx), f"{WEEKDAYS[idx]} of next week"
    if p in {"eom", "end of month"}:
        nxt = date(today.year + (today.month == 12), today.month % 12 + 1, 1)
        return nxt - timedelta(days=1), "last day of this month"
    if m := re.fullmatch(r"in (\d+) (day|days|week|weeks)", p):
        n = int(m[1]) * (7 if m[2].startswith("week") else 1)
        return today + timedelta(days=n), f"{n} days from today"

    # 'fri', 'next wed', 'this thu'
    if m := re.fullmatch(r"(next |this )?([a-z]+\.?)", p):
        idx = _weekday_index(m[2])
        if idx is not None:
            if m[1] == "next ":  # the same weekday in the NEXT calendar week (Mon-Sun)
                monday_next = today - timedelta(days=today.weekday()) + timedelta(days=7)
                return monday_next + timedelta(days=idx), f"{WEEKDAYS[idx]} of next week"
            delta = (idx - today.weekday()) % 7 or 7  # bare weekday = the next one coming up
            return today + timedelta(days=delta), f"the next {WEEKDAYS[idx]} coming up"

    # '10/14' (month/day, US style), 'oct 14', '14 oct'
    if m := re.fullmatch(r"(\d{1,2})/(\d{1,2})", p):
        month, day = int(m[1]), int(m[2])
        if 1 <= month <= 12 and 1 <= day <= 31:
            return _roll_year(month, day, today), "read as month/day"
    if m := re.fullmatch(r"([a-z]{3})[a-z]*\.? (\d{1,2})", p) or re.fullmatch(r"(\d{1,2}) ([a-z]{3})[a-z]*\.?", p):
        a, b = m[1], m[2]
        month_word, day = (a, b) if a.isalpha() else (b, a)
        if month_word in MONTHS:
            return _roll_year(MONTHS.index(month_word) + 1, int(day), today), "month name + day"

    return None, f"could not understand {phrase!r}"


def _roll_year(month: int, day: int, today: date) -> date:
    """A bare 'Oct 14' means the next time that date occurs (this year, or next if already past)."""
    candidate = date(today.year, month, day)
    return candidate if candidate >= today else date(today.year + 1, month, day)


# ------------------------------------------------------------------ finding dates and times inside messy board text

TIME_12H = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", re.IGNORECASE)
TIME_24H = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")
AT_HOUR = re.compile(r"\bat (\d{1,2})\b(?!\s*(?::|am\b|pm\b))", re.IGNORECASE)


def find_date(text: str, today: date) -> tuple[date | None, str]:
    """Find a date phrase INSIDE messy text: 'by Fri' -> Friday, 'Sprint review next Tue 11am - ALL' -> next Tuesday."""
    if iso := re.search(r"\d{4}-\d{2}-\d{2}", text):
        return resolve_date(iso.group(), today)
    cleaned = TIME_24H.sub(" ", TIME_12H.sub(" ", re.sub(r"\(.*?\)", " ", text)))
    cleaned = re.sub(r"(?<=[A-Za-z])(?=\d)|(?<=\d)(?=[A-Za-z])", " ", cleaned)  # glued words: 'due10/14' -> 'due 10/14'
    words = [w.strip(".") for w in re.findall(r"[A-Za-z0-9/\.]+", cleaned)]  # 'week.' at the end of a sentence -> 'week'
    words = [w for w in words if w]
    for size in (4, 3, 2, 1):  # longest phrases first, so 'Tuesday next week' wins over 'next week'
        for i in range(len(words) - size + 1):
            found, why = resolve_date(" ".join(words[i : i + size]), today)
            if found:
                return found, why
    return None, f"no date found in {text!r}"


def find_time(text: str) -> time | None:
    """'3pm' -> 15:00, '11:30 am' -> 11:30, '15:00' -> 15:00."""
    if m := TIME_12H.search(text):
        hour, minute, ampm = int(m[1]) % 12, int(m[2] or 0), m[3].lower()
        return time(hour + (12 if ampm == "pm" else 0), minute) if 1 <= int(m[1]) <= 12 else None
    if m := TIME_24H.search(text):
        return time(int(m[1]), int(m[2]))
    if m := AT_HOUR.search(text):  # 'at 3' / 'at 11' with no am/pm: meetings are in working hours, so 1-6 means afternoon
        hour = int(m[1])
        if 1 <= hour <= 12:
            return time(hour + 12 if hour <= 6 else hour, 0)
    return None


def resolve_when(text: str, today: date) -> datetime | None:
    """'Thu 3pm' -> the datetime of the next Thursday at 15:00. None unless BOTH a day and a time are found."""
    day, clock = find_date(text, today)[0], find_time(text)
    return datetime.combine(day, clock) if day and clock else None
