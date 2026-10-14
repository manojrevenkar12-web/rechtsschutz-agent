"""Deterministic deadline engine (Fristenrechner).

The LLM never does date arithmetic. It only extracts *which* document the client received and
*when* it was received; this module turns that into a deadline using the statutory rules.

Supported deadline-triggering documents
---------------------------------------
kuendigung_arbeitsvertrag   Kündigungsschutzklage: 3 weeks after Zugang (§ 4 S. 1 KSchG)
bussgeldbescheid            Einspruch: 2 weeks after Zustellung (§ 67 Abs. 1 OWiG)
mahnbescheid                Widerspruch: 2 weeks after Zustellung (§ 692 Abs. 1 Nr. 3 ZPO)
vollstreckungsbescheid      Einspruch: 2 weeks Notfrist after Zustellung (§§ 700, 339 ZPO)
sozialbescheid              Widerspruch: 1 month after Bekanntgabe (§ 84 Abs. 1 SGG)
mieterhoehungsverlangen     Zustimmungsfrist: end of the 2nd calendar month after Zugang (§ 558b Abs. 1 BGB)

Computation
-----------
* Week and month periods: the event day is not counted (§ 187 Abs. 1 BGB); a period of weeks ends
  on the weekday with the same name as the event day, a period of months on the day with the same
  number, or the month's last day if that day does not exist (§ 188 Abs. 2, 3 BGB; § 64 SGG).
* If the last day is a Saturday, Sunday or public holiday at the place of the court or authority,
  the period ends on the next working day (§ 193 BGB, § 222 Abs. 2 ZPO, § 43 Abs. 2 StPO, § 64 Abs. 3 SGG).

Scope note: this is a triage aid, not legal advice. It assumes the client-reported receipt date is
the legally relevant one; special cases (Bekanntgabe fiction, Zugang during absence, § 5 KSchG late
admission) are surfaced as review flags, not decided here.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum
from functools import lru_cache

import holidays


class EventType(str, Enum):
    KUENDIGUNG_ARBEITSVERTRAG = "kuendigung_arbeitsvertrag"
    BUSSGELDBESCHEID = "bussgeldbescheid"
    MAHNBESCHEID = "mahnbescheid"
    VOLLSTRECKUNGSBESCHEID = "vollstreckungsbescheid"
    SOZIALBESCHEID = "sozialbescheid"
    MIETERHOEHUNGSVERLANGEN = "mieterhoehungsverlangen"
    KEIN_FRISTEREIGNIS = "kein_fristereignis"


@dataclass(frozen=True)
class Rule:
    action: str
    legal_basis: str
    weeks: int = 0
    months: int = 0
    end_of_calendar_month: bool = False


RULES: dict[EventType, Rule] = {
    EventType.KUENDIGUNG_ARBEITSVERTRAG: Rule("Kündigungsschutzklage erheben", "§ 4 S. 1 KSchG", weeks=3),
    EventType.BUSSGELDBESCHEID: Rule("Einspruch einlegen", "§ 67 Abs. 1 OWiG", weeks=2),
    EventType.MAHNBESCHEID: Rule("Widerspruch einlegen", "§ 692 Abs. 1 Nr. 3 ZPO", weeks=2),
    EventType.VOLLSTRECKUNGSBESCHEID: Rule("Einspruch einlegen", "§§ 700, 339 ZPO", weeks=2),
    EventType.SOZIALBESCHEID: Rule("Widerspruch einlegen", "§ 84 Abs. 1 SGG", months=1),
    EventType.MIETERHOEHUNGSVERLANGEN: Rule(
        "Zustimmung erklären oder verweigern", "§ 558b Abs. 1 BGB", months=2, end_of_calendar_month=True
    ),
}


@dataclass(frozen=True)
class Deadline:
    event_type: EventType
    event_date: date
    raw_end: date          # end of the period before the weekend/holiday shift
    deadline: date         # last day to act
    shifted: bool
    action: str
    legal_basis: str
    explanation: str

    def as_dict(self) -> dict:
        return {
            "event_type": self.event_type.value,
            "event_date": self.event_date.isoformat(),
            "deadline": self.deadline.isoformat(),
            "raw_end": self.raw_end.isoformat(),
            "shifted": self.shifted,
            "action": self.action,
            "legal_basis": self.legal_basis,
            "explanation": self.explanation,
        }


@lru_cache(maxsize=32)
def _holidays(subdiv: str, year: int) -> holidays.HolidayBase:
    return holidays.Germany(subdiv=subdiv, years=[year, year + 1], language="de")


def is_non_working_day(d: date, subdiv: str = "BE") -> bool:
    return d.weekday() >= 5 or d in _holidays(subdiv, d.year)


def add_months(d: date, months: int) -> date:
    """§ 188 Abs. 2, 3 BGB: same day number, or the last day of the month if it does not exist."""
    m = d.month - 1 + months
    y, m = d.year + m // 12, m % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def end_of_month_after(d: date, months: int) -> date:
    m = d.month - 1 + months
    y, m = d.year + m // 12, m % 12 + 1
    return date(y, m, calendar.monthrange(y, m)[1])


def next_working_day(d: date, subdiv: str = "BE") -> date:
    while is_non_working_day(d, subdiv):
        d += timedelta(days=1)
    return d


def compute_deadline(event_type: EventType | str, event_date: date, subdiv: str = "BE") -> Deadline | None:
    event_type = EventType(event_type)
    rule = RULES.get(event_type)
    if rule is None:
        return None
    if rule.end_of_calendar_month:
        raw = end_of_month_after(event_date, rule.months)
    elif rule.months:
        raw = add_months(event_date, rule.months)
    else:
        raw = event_date + timedelta(weeks=rule.weeks)
    final = next_working_day(raw, subdiv)
    shifted = final != raw
    why = f"{rule.action} ({rule.legal_basis}); Ereignis {event_date:%d.%m.%Y} → Fristende {raw:%d.%m.%Y}"
    if shifted:
        why += f", verschoben auf den nächsten Werktag {final:%d.%m.%Y} (Wochenende/Feiertag, § 193 BGB)"
    return Deadline(event_type, event_date, raw, final, shifted, rule.action, rule.legal_basis, why)


# ---- calendar tools exposed to the LLM for resolving relative dates ("letzten Donnerstag") ----

WEEKDAYS_DE = ["montag", "dienstag", "mittwoch", "donnerstag", "freitag", "samstag", "sonntag"]


def shift_days(base: date, days: int) -> date:
    return base + timedelta(days=days)


def most_recent_weekday(base: date, weekday: str, include_today: bool = False) -> date:
    """Most recent `weekday` strictly before `base` (or on it if include_today)."""
    target = WEEKDAYS_DE.index(weekday.lower())
    delta = (base.weekday() - target) % 7
    if delta == 0 and not include_today:
        delta = 7
    return base - timedelta(days=delta)


def weekday_in_week(base: date, weekday: str, weeks_offset: int = 0) -> date:
    """`weekday` in the calendar week of `base` shifted by `weeks_offset` (−1 = last week)."""
    target = WEEKDAYS_DE.index(weekday.lower())
    monday = base - timedelta(days=base.weekday()) + timedelta(weeks=weeks_offset)
    return monday + timedelta(days=target)
