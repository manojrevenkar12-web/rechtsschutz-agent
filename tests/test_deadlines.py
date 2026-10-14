from datetime import date
import pytest
from rsagent.deadlines import compute_deadline, add_months, most_recent_weekday, weekday_in_week, is_non_working_day

@pytest.mark.parametrize("etype,event,expected,shifted", [
    # § 4 KSchG, 3 weeks: Fri 18.09.2026 -> Fri 09.10.2026
    ("kuendigung_arbeitsvertrag", date(2026, 9, 18), date(2026, 10, 9), False),
    # Sat 12.09.2026 + 3 weeks = Sat 03.10.2026 (also Tag der Deutschen Einheit) -> Mon 05.10.2026
    ("kuendigung_arbeitsvertrag", date(2026, 9, 12), date(2026, 10, 5), True),
    # § 67 OWiG: Fri 11.12.2026 + 2 weeks = Fri 25.12 (holiday) -> 26.12 (holiday, Sat) -> 27.12 (Sun) -> Mon 28.12
    ("bussgeldbescheid", date(2026, 12, 11), date(2026, 12, 28), True),
    # § 692 ZPO: Wed 30.09.2026 -> Wed 14.10.2026
    ("mahnbescheid", date(2026, 9, 30), date(2026, 10, 14), False),
    # §§ 700, 339 ZPO: Thu 17.12.2026 -> Thu 31.12.2026 (New Year's Eve is a working day)
    ("vollstreckungsbescheid", date(2026, 12, 17), date(2026, 12, 31), False),
    # § 64 SGG: 31.08.2026 + 1 month -> 30.09.2026 (no 31 September)
    ("sozialbescheid", date(2026, 8, 31), date(2026, 9, 30), False),
    # 11.09.2026 + 1 month = Sun 11.10.2026 -> Mon 12.10.2026
    ("sozialbescheid", date(2026, 9, 11), date(2026, 10, 12), True),
    # § 558b BGB: received in September -> end of November
    ("mieterhoehungsverlangen", date(2026, 9, 30), date(2026, 11, 30), False),
    # received 03.11.2026 -> end of January 2027 = Sun 31.01.2027 -> Mon 01.02.2027
    ("mieterhoehungsverlangen", date(2026, 11, 3), date(2027, 2, 1), True),
])
def test_hand_computed_deadlines(etype, event, expected, shifted):
    d = compute_deadline(etype, event)
    assert d.deadline == expected
    assert d.shifted is shifted

def test_no_rule_for_non_deadline_events():
    assert compute_deadline("kein_fristereignis", date(2026, 9, 1)) is None

def test_holidays_are_state_specific():
    # Reformationstag is a holiday in Brandenburg, not in Berlin
    assert is_non_working_day(date(2026, 10, 30) .replace(day=31), "BB")
    assert is_non_working_day(date(2026, 10, 31), "BE")  # it's a Saturday in 2026 anyway
    d_be = compute_deadline("bussgeldbescheid", date(2027, 10, 15), "BE")
    d_bb = compute_deadline("bussgeldbescheid", date(2027, 10, 15), "BB")
    assert d_be.deadline == date(2027, 10, 29) and d_bb.deadline == date(2027, 10, 29)
    d_bb2 = compute_deadline("bussgeldbescheid", date(2027, 10, 17), "BB")  # Sun 31.10.2027 Reformationstag -> Mon 01.11
    assert d_bb2.deadline == date(2027, 11, 1)

def test_add_months_end_of_month():
    assert add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert add_months(date(2028, 1, 31), 1) == date(2028, 2, 29)

def test_relative_date_tools():
    mon = date(2026, 10, 12)
    assert most_recent_weekday(mon, "donnerstag") == date(2026, 10, 8)
    assert most_recent_weekday(mon, "montag") == date(2026, 10, 5)  # strictly before
    assert weekday_in_week(date(2026, 10, 14), "dienstag", -1) == date(2026, 10, 6)
    assert weekday_in_week(date(2026, 9, 30), "montag", 0) == date(2026, 9, 28)

def test_gold_deadlines_reproduced(all_cases):
    for c in all_cases:
        g = c["gold"]
        d = compute_deadline(g["event_type"], g["event_date"]) if g["event_date"] else None
        assert (d.deadline if d else None) == g["deadline"], c["id"]
