from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote_plus

from jinja2 import Environment, FileSystemLoader


ROOT = Path(__file__).resolve().parents[1]
THEME_VALUES = {
    "jade",
    "steel",
    "moss",
    "rose",
    "amber",
    "daylight",
    "paper",
    "mint",
    "sky",
    "peach",
    "track-colours",
    "aurora",
    "sunset",
    "ocean",
    "berry",
    "candy",
    "high-contrast",
    "race-night",
    "garden",
    "studio",
}


def datetime_filter(value: object, fmt: str = "%a %d %b %H:%M") -> str:
    if hasattr(value, "strftime"):
        return value.strftime(fmt)  # type: ignore[no-any-return]
    return str(value or "")


def render_day_template() -> None:
    env = Environment(loader=FileSystemLoader(ROOT / "app" / "templates"))
    env.filters.update(
        datetime=datetime_filter,
        time=str,
        day_short=str,
        hours=str,
        urlencode=quote_plus,
    )
    env.globals["theme_values"] = THEME_VALUES
    for template_name in [
        "admin.html",
        "base.html",
        "day.html",
        "login.html",
        "month.html",
        "roster_day_builder.html",
        "settings.html",
        "signup.html",
        "timesheet.html",
    ]:
        env.get_template(template_name)
    template = env.get_template("day.html")
    html = template.render(
        request={},
        notice=None,
        current_user=None,
        date_text="2026-06-13",
        day_date=date(2026, 6, 13),
        day_holiday={"is_public_holiday": True, "names": ["Waitangi Day"], "name": "Waitangi Day", "aria_label": "Public holiday: Waitangi Day"},
        month_year=2026,
        month_number=6,
        deputy_schedule_changed=False,
        deputy_schedule_people=[],
        deputy_schedule_changes=[],
        deputy_event_changes=[],
        deputy_assignment_history=[],
        deputy_schedule_label="Deputy Schedule",
        track_maps=[
            {
                "track_label": "Te Rapa",
                "course_label": "Te Rapa",
                "image_url": "/track-map/terapa",
                "image_width": 1200,
                "image_height": 800,
            }
        ],
        shifts=[
            {
                "id": 1,
                "deleted_from_source": 0,
                "colour_style": "--shift-location-colour: var(--location-colour-8); --location-colour: var(--location-colour-8);",
                "time_range": "08:30-17:45",
                "role_chain_label": "Sound/VT",
                "role_full_label": "Sound/VT",
                "role_label": "SVT",
                "title": "[TRAP-T] SVT",
                "track_label": "Te Rapa",
                "race_type_label": "Thoroughbred racing",
                "location": "12 Sir Tristram Avenue",
                "changed_since_viewed": 1,
                "change_summary_text": "Rostered hours changed",
                "source_status": "",
                "timing_adjustment_labels": [],
                "start_at": "2026-06-13T08:30:00+12:00",
                "end_at": "2026-06-13T17:45:00+12:00",
                "display_hours_label": "9h 15m",
                "source_link": "",
                "race_day_summary": {
                    "has_items": True,
                    "rows": [
                        {"label": "Clow Place", "value": "08:30"},
                        {"label": "On track", "value": "08:45"},
                        {"label": "Records", "value": "10:30"},
                        {"label": "Live", "value": "11:00"},
                        {"label": "10 races", "value": "11:10 | 16:24"},
                    ],
                },
                "description_lines": ["10 races 1110 | 1624"],
                "roster_summary": {"has_structured": True},
                "changes": [],
                "timing_math": {
                    "segments": [],
                    "start_label": "08:30",
                    "end_label": "17:45",
                    "raw_label": "9h 15m",
                    "race_day": {
                        "available": True,
                        "complete": True,
                        "lines": [
                            {"label": "Clow Place", "value": "08:30"},
                            {"label": "Back at base", "value": "17:45"},
                        ],
                        "formula": "Clow Place 08:30 to on track 08:45; return travel gives 17:45.",
                    },
                },
                "private_note": "",
                "timing_adjustment_time": "",
                "timing_adjustment_last_race": 0,
                "timing_adjustment_day_finished": 0,
            }
        ],
    )
    if "Race Day" not in html or "11:10 | 16:24" not in html:
        raise AssertionError("Day template did not render expected race-day content.")
    if "--shift-location-colour: var(--location-colour-8)" not in html:
        raise AssertionError("Day template did not render per-shift location colour style.")
    if 'src="/track-map/terapa"' not in html or 'alt="Te Rapa racecourse 2D track map"' not in html:
        raise AssertionError("Day template did not render the cached track map.")
    if 'aria-label="Public holiday: Waitangi Day"' not in html:
        raise AssertionError("Day template did not render an accessible public-holiday marker.")


def render_month_template() -> None:
    env = Environment(loader=FileSystemLoader(ROOT / "app" / "templates"))
    env.filters.update(
        datetime=datetime_filter,
        time=str,
        day_short=str,
        hours=str,
        urlencode=quote_plus,
    )
    env.globals["theme_values"] = THEME_VALUES
    template = env.get_template("month.html")
    shift = {
        "id": 1,
        "date": "2026-06-13",
        "deleted_from_source": 0,
        "changed_since_viewed": 0,
        "colour_style": "--shift-location-colour: var(--location-colour-8); --location-colour: var(--location-colour-8);",
        "track_label": "Te Rapa",
        "role_chain_label": "Sound/VT",
        "role_label": "SVT",
        "title": "[TRAP-T] SVT",
        "start_label": "08:30",
        "time_range": "08:30-17:45",
        "display_hours_label": "9h 15m",
        "race_type_label": "Thoroughbred racing",
    }
    day = {
        "date": date(2026, 6, 13),
        "iso": "2026-06-13",
        "day_number": 13,
        "in_month": True,
        "is_today": False,
        "shifts": [shift],
        "holiday": {"is_public_holiday": True, "names": ["Waitangi Day"], "name": "Waitangi Day", "aria_label": "Public holiday: Waitangi Day"},
        "open_shifts": [],
        "timesheet": None,
    }
    html = template.render(
        request=SimpleNamespace(url=SimpleNamespace(path="/month", query=""), cookies={}),
        notice=None,
        current_user=None,
        header_context="June 2026",
        header_prev_url="/month?year=2026&month=5",
        header_next_url="/month?year=2026&month=7",
        month_view_url="/month?year=2026&month=6&view=month",
        list_view_url="/month?year=2026&month=6&view=list",
        view="month",
        month_name="June 2026",
        weekdays=["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
        weeks=[{"days": [day], "total": 9.25}],
        active_days=[day],
        upcoming_shifts=[shift],
        today=date(2026, 6, 14),
    )
    if 'class="shift-card' not in html:
        raise AssertionError("Month template did not render a calendar shift card.")
    if "--shift-location-colour: var(--location-colour-8)" not in html:
        raise AssertionError("Month template did not render per-shift location colour style.")
    if html.count('aria-label="Public holiday: Waitangi Day"') != 1:
        raise AssertionError("Month date should render exactly one holiday marker regardless of shift count.")
    if 'class="calendar-date-heading"' not in html:
        raise AssertionError("Month holiday marker is not in reserved date-heading layout space.")

    list_html = template.render(
        request=SimpleNamespace(url=SimpleNamespace(path="/month", query="scope=global"), cookies={}),
        notice=None, current_user=None, view="list", global_view=True,
        active_days=[day], weeks=[], upcoming_shifts=[], today=date(2026, 6, 14),
        month_name="June 2026", header_prev_url="#", header_next_url="#",
        month_view_url="#", list_view_url="#",
    )
    if 'aria-label="Public holiday: Waitangi Day"' not in list_html:
        raise AssertionError("Shared/global month list did not render the holiday marker.")
    if 'class="list-day-heading"' not in list_html:
        raise AssertionError("List holiday marker is not in reserved date-heading layout space.")


def render_timesheet_template() -> None:
    env = Environment(loader=FileSystemLoader(ROOT / "app" / "templates"))
    env.filters.update(datetime=datetime_filter, time=str, day_short=str, hours=str, urlencode=quote_plus)
    env.globals["theme_values"] = THEME_VALUES
    html = env.get_template("timesheet.html").render(
        request={}, notice=None, current_user=None, month_year=2026, month_number=2,
        summary={
            "period_label": "24 Jan-06 Feb 2026", "total": 0,
            "days": [{
                "iso": "2026-02-06", "date_label": "Fri 06 Feb", "total": 0,
                "locations": "-", "shifts": [], "notes": [],
                "holiday": {"is_public_holiday": True, "names": ["Waitangi Day"], "name": "Waitangi Day", "aria_label": "Public holiday: Waitangi Day"},
            }],
        },
    )
    if 'aria-label="Public holiday: Waitangi Day"' not in html:
        raise AssertionError("Timesheet row did not render the holiday marker.")
    if 'class="timesheet-date-heading"' not in html:
        raise AssertionError("Timesheet holiday marker is not in reserved date-heading layout space.")


def check_holiday_marker_css() -> None:
    css = (ROOT / "app" / "static" / "style.css").read_text(encoding="utf-8")
    if ".calendar-date-heading" not in css or "calc(100vw - 24px)" not in css:
        raise AssertionError("Holiday marker layout or narrow-viewport popover constraint is missing.")
    if ".day-cell > .holiday-marker" in css or ".timesheet-day > .holiday-marker" in css:
        raise AssertionError("Holiday markers must not be absolutely positioned over date headings.")


if __name__ == "__main__":
    render_day_template()
    render_month_template()
    render_timesheet_template()
    check_holiday_marker_css()
    print("template smoke render ok")
