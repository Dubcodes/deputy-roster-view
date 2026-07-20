from __future__ import annotations

import calendar
from datetime import date, timedelta
from functools import lru_cache


# Dates prescribed by the Te Ture mo Te Hararei Tumatanui o te Kāhui o
# Matariki 2022 / Matariki Public Holiday Act 2022. The table is finite because
# the astronomical date is officially set rather than derived by this app.
MATARIKI_DATES = {
    2022: (6, 24), 2023: (7, 14), 2024: (6, 28), 2025: (6, 20),
    2026: (7, 10), 2027: (6, 25), 2028: (7, 14), 2029: (7, 6),
    2030: (6, 21), 2031: (7, 11), 2032: (7, 2), 2033: (6, 24),
    2034: (7, 7), 2035: (6, 29), 2036: (7, 18), 2037: (7, 10),
    2038: (6, 25), 2039: (7, 15), 2040: (7, 6), 2041: (7, 19),
    2042: (7, 11), 2043: (7, 3), 2044: (6, 24), 2045: (7, 7),
    2046: (6, 29), 2047: (7, 19), 2048: (7, 3), 2049: (6, 25),
    2050: (7, 15), 2051: (6, 30), 2052: (6, 21),
}


def _easter_sunday(year: int) -> date:
    """Gregorian computus, valid for the years used by this app."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _nth_weekday(year: int, month: int, weekday: int, nth: int) -> date:
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (nth - 1))


def _nearest_monday(value: date) -> date:
    offset = -value.weekday() if value.weekday() <= calendar.THURSDAY else 7 - value.weekday()
    return value + timedelta(days=offset)


def _observed_pair(first: date, second: date) -> tuple[date, date]:
    if first.weekday() == calendar.SATURDAY:
        return first + timedelta(days=2), second + timedelta(days=2)
    if first.weekday() == calendar.SUNDAY:
        return first + timedelta(days=1), second + timedelta(days=1)
    return first, second


def _mondayised(value: date) -> date:
    if value.weekday() == calendar.SATURDAY:
        return value + timedelta(days=2)
    if value.weekday() == calendar.SUNDAY:
        return value + timedelta(days=1)
    return value


def _add(rows: dict[date, list[str]], day_value: date, name: str) -> None:
    if name not in rows.setdefault(day_value, []):
        rows[day_value].append(name)


@lru_cache(maxsize=128)
def holidays_for_year(year: int, region: str = "") -> dict[date, tuple[str, ...]]:
    rows: dict[date, list[str]] = {}
    new_year = date(year, 1, 1)
    new_year_two = date(year, 1, 2)
    _add(rows, new_year, "New Year's Day")
    _add(rows, new_year_two, "Day after New Year's Day")
    observed_new_year, observed_new_year_two = _observed_pair(new_year, new_year_two)
    if observed_new_year != new_year:
        _add(rows, observed_new_year, "New Year's Day (observed)")
    if observed_new_year_two != new_year_two:
        _add(rows, observed_new_year_two, "Day after New Year's Day (observed)")

    for actual, name in (
        (date(year, 2, 6), "Waitangi Day"),
        (date(year, 4, 25), "ANZAC Day"),
    ):
        _add(rows, actual, name)
        observed = _mondayised(actual)
        if observed != actual:
            _add(rows, observed, f"{name} (observed)")

    easter = _easter_sunday(year)
    _add(rows, easter - timedelta(days=2), "Good Friday")
    _add(rows, easter + timedelta(days=1), "Easter Monday")
    _add(rows, _nth_weekday(year, 6, calendar.MONDAY, 1), "King's Birthday")
    if year in MATARIKI_DATES:
        month, day = MATARIKI_DATES[year]
        _add(rows, date(year, month, day), "Matariki")
    _add(rows, _nth_weekday(year, 10, calendar.MONDAY, 4), "Labour Day")

    christmas = date(year, 12, 25)
    boxing = date(year, 12, 26)
    _add(rows, christmas, "Christmas Day")
    _add(rows, boxing, "Boxing Day")
    observed_christmas, observed_boxing = _observed_pair(christmas, boxing)
    if observed_christmas != christmas:
        _add(rows, observed_christmas, "Christmas Day (observed)")
    if observed_boxing != boxing:
        _add(rows, observed_boxing, "Boxing Day (observed)")

    region_key = region.strip().lower().replace("_", "-")
    if region_key in {"auckland", "northland", "waikato", "bay-of-plenty", "gisborne"}:
        _add(rows, _nearest_monday(date(year, 1, 29)), "Auckland Anniversary Day")
    elif region_key == "wellington":
        _add(rows, _nearest_monday(date(year, 1, 22)), "Wellington Anniversary Day")
    elif region_key in {"nelson", "tasman", "buller's"}:
        _add(rows, _nearest_monday(date(year, 2, 1)), "Nelson Anniversary Day")
    elif region_key == "taranaki":
        _add(rows, _nth_weekday(year, 3, calendar.MONDAY, 2), "Taranaki Anniversary Day")
    elif region_key in {"hawkes-bay", "hawke's-bay"}:
        _add(rows, _nth_weekday(year, 10, calendar.MONDAY, 4) - timedelta(days=3), "Hawke's Bay Anniversary Day")
    elif region_key == "otago":
        _add(rows, _nearest_monday(date(year, 3, 23)), "Otago Anniversary Day")
    elif region_key == "southland":
        _add(rows, easter + timedelta(days=2), "Southland Anniversary Day")

    return {day_value: tuple(names) for day_value, names in rows.items()}


def holiday_for_date(day_value: date, region: str = "") -> dict[str, object]:
    names = list(holidays_for_year(day_value.year, region).get(day_value, ()))
    return {
        "is_public_holiday": bool(names),
        "names": names,
        "name": " / ".join(names),
        "aria_label": "Public holiday: " + "; ".join(names) if names else "",
    }
