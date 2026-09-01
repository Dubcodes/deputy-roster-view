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

SIMPLIFIED_CALCULATION_LINES = [
    {"label": "Start · Office / Clow Place", "value": "08:30"},
    {"label": "On track", "value": "09:30"},
    {"label": "Outbound travel", "value": "1h"},
    {"label": "Deputy roster start", "value": "09:30"},
    {"label": "Last race", "value": "16:35"},
    {"label": "Race cleared", "value": "16:45"},
    {"label": "Pack-up done", "value": "17:45"},
    {"label": "Return travel", "value": "1h"},
    {"label": "Finish · Office / Clow Place", "value": "18:45"},
    {"label": "Calculated total", "value": "10h 15m"},
]


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
        deputy_event_change_groups=[{
            "changed_at_label": "25 Jul 20:45",
            "lines": [
                "Nate moved CCU2 → Head On, replacing Campbell Stephens",
                "CCU2 is now TBC",
            ],
        }],
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
                "time_range": "09:30–18:30",
                "display_window": {
                    "source": "roster",
                    "start_label": "09:30",
                    "end_label": "18:30",
                    "hours_label": "9h",
                },
                "role_chain_label": "Sound/VT",
                "role_full_label": "Sound/VT",
                "role_label": "SVT",
                "title": "[TRAP-T] SVT",
                "track_label": "Te Rapa",
                "race_type_label": "Thoroughbred racing",
                "location": "12 Sir Tristram Avenue",
                "changed_since_viewed": 1,
                "change_summary_text": "Start 09:00 → 09:30 · Finish 18:00 → 18:30",
                "source_status": "",
                "timing_adjustment_labels": [],
                "start_at": "2026-06-13T09:30:00+12:00",
                "end_at": "2026-06-13T18:30:00+12:00",
                "display_hours_label": "9h",
                "source_link": "",
                "race_day_summary": {
                    "has_items": True,
                    "source_note": "Race count from Deputy · Race times from Love Racing",
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
                "changes": [
                    {
                        "field_label": "Start time",
                        "old_display": "09:00",
                        "new_display": "09:30",
                        "changed_at": "2026-07-25T20:45:00+12:00",
                    },
                    {
                        "field_label": "Finish time",
                        "old_display": "18:00",
                        "new_display": "18:30",
                        "changed_at": "2026-07-25T20:45:00+12:00",
                    },
                ],
                "timing_math": {
                    "segments": [],
                    "start_label": "08:30",
                    "end_label": "17:45",
                    "raw_label": "9h 15m",
                    "race_day": {
                        "available": True,
                        "complete": True,
                        "lines": SIMPLIFIED_CALCULATION_LINES,
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
    if "Race count from Deputy · Race times from Love Racing" not in html:
        raise AssertionError("Day template did not render compact Love Racing source evidence.")
    if "--shift-location-colour: var(--location-colour-8)" not in html:
        raise AssertionError("Day template did not render per-shift location colour style.")
    if 'src="/track-map/terapa"' not in html or 'alt="Te Rapa racecourse 2D track map"' not in html:
        raise AssertionError("Day template did not render the cached track map.")
    if 'aria-label="Public holiday: Waitangi Day"' not in html:
        raise AssertionError("Day template did not render an accessible public-holiday marker.")
    if "Start · Office / Clow Place" not in html or "Finish · Office / Clow Place" not in html:
        raise AssertionError("Day template did not render simplified start/finish wording.")
    if "Deputy roster start" not in html:
        raise AssertionError("Day template hid an operational timing discrepancy.")
    if "Nate moved CCU2 → Head On, replacing Campbell Stephens" not in html or "CCU2 is now TBC" not in html:
        raise AssertionError("Day template did not render grouped crew-change history.")
    if (
        "Start 09:00 → 09:30 · Finish 18:00 → 18:30" not in html
        or "Start time" not in html
        or "09:00 → 09:30" not in html
        or "Finish time" not in html
        or "18:00 → 18:30" not in html
    ):
        raise AssertionError("Day template did not render compact personal roster changes.")
    if "Deputy roster: 09:30–18:30 = 9h" not in html or "Calculated operational timing (supplemental)" not in html:
        raise AssertionError("Day template did not distinguish the Deputy roster window from calculated timing.")
    if any(label in html for label in ("Start origin evidence", "Finish destination evidence", "roster base timing")):
        raise AssertionError("Day template rendered internal timeline evidence labels.")


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
        "track_label": "Ruakaka",
        "role_chain_label": "Director",
        "role_label": "DIR",
        "title": "[T-Ruakaka] DIR",
        "start_label": "09:00",
        "display_start_label": "09:00",
        "time_range": "09:00–22:00",
        "display_hours_label": "13h",
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
        weeks=[{"days": [day], "total": 13.0}],
        active_days=[day],
        upcoming_shifts=[shift],
        upcoming_items=[{**shift, "upcoming_kind": "deputy"}],
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
    if (
        "13h" not in html
        or html.count("09:00") < 2
        or "Director · 09:00" not in html
        or "05:00" in html
    ):
        raise AssertionError(
            f"Month/list/Next Up did not retain the primary Deputy roster window: "
            f"09:00={html.count('09:00')}, hours={'13h' in html}, "
            f"next={'Director · 09:00' in html}, inferred={'05:00' in html}"
        )

    list_html = template.render(
        request=SimpleNamespace(url=SimpleNamespace(path="/month", query="scope=global"), cookies={}),
        notice=None, current_user=None, view="list", global_view=True,
        active_days=[day], weeks=[], upcoming_shifts=[], upcoming_items=[], today=date(2026, 6, 14),
        month_name="June 2026", header_prev_url="#", header_next_url="#",
        month_view_url="#", list_view_url="#",
    )
    if 'aria-label="Public holiday: Waitangi Day"' not in list_html:
        raise AssertionError("Shared/global month list did not render the holiday marker.")
    if 'class="list-day-heading"' not in list_html:
        raise AssertionError("List holiday marker is not in reserved date-heading layout space.")
    if "09:00–22:00" not in list_html or "13h" not in list_html or "05:00" in list_html:
        raise AssertionError("List view did not retain the primary Deputy roster window.")


def render_roster_note_template() -> None:
    env = Environment(loader=FileSystemLoader(ROOT / "app" / "templates"))
    env.filters.update(datetime=datetime_filter, time=str, day_short=str, hours=str, urlencode=quote_plus)
    env.globals["theme_values"] = THEME_VALUES
    template = env.get_template("day.html")

    def render_note(description: str, *, has_structured: bool = False, race_day: bool = False) -> str:
        lines = description.splitlines() if description else []
        shift = {
            "id": 12,
            "deleted_from_source": 0,
            "colour_style": "--shift-location-colour: var(--location-colour-8);",
            "time_range": "09:00–18:00",
            "display_hours_label": "9h",
            "role_chain_label": "SVT",
            "role_full_label": "SVT",
            "role_label": "SVT",
            "title": "SVT",
            "track_label": "Te Aroha",
            "location": "Te Aroha",
            "race_type_label": "",
            "changed_since_viewed": 0,
            "source_status": "",
            "timing_adjustment_labels": [],
            "description": description,
            "description_lines": lines,
            "roster_summary": {"has_structured": has_structured},
            "race_day_summary": {
                "has_items": race_day,
                "source_note": "Race count from Deputy",
                "rows": [{"label": "8 races", "value": "11:00 | 16:00", "source": "Deputy"}],
            },
            "changes": [],
            "timing_math": {
                "segments": [],
                "start_label": "09:00",
                "end_label": "18:00",
                "raw_label": "9h",
                "race_day": {"available": False, "complete": False, "lines": []},
            },
        }
        return template.render(
            request={}, notice=None, current_user=None,
            date_text="2026-08-30", day_date=date(2026, 8, 30), month_year=2026, month_number=8,
            day_holiday={"is_public_holiday": False}, shifts=[shift], open_shifts=[],
            deputy_schedule_changed=False, deputy_schedule_people=[], deputy_schedule_changes=[],
            deputy_event_changes=[], deputy_event_change_groups=[], deputy_assignment_history=[],
            deputy_schedule_label="Deputy Schedule", track_maps=[], can_change_self_travel=False,
        )

    te_aroha_note = "*Rescheduled from Te Aroha 30th August\nIf you can't work this new date, please advise your manager"
    note_html = render_note(te_aroha_note)
    if "Current roster note" not in note_html or te_aroha_note.splitlines()[0] not in note_html or te_aroha_note.splitlines()[1] not in note_html:
        raise AssertionError("Current roster note did not retain the Te Aroha source lines.")
    if 'class="roster-note-panel"' not in note_html or 'class="roster-note-lines"' not in note_html:
        raise AssertionError("Current roster note did not render as one cohesive note panel.")
    if '<details class="raw-source raw-roster-note">' not in note_html or "<summary>Raw roster note</summary>" not in note_html:
        raise AssertionError("Raw roster note control is missing for an unstructured source note.")
    if f'<div class="raw-roster-note-content">{te_aroha_note}</div>' not in note_html:
        raise AssertionError("Raw roster note did not retain the persisted source text and line break.")
    if '<ul class="roster-lines">' in note_html:
        raise AssertionError("Roster-note panel must not use divider-oriented roster list markup.")

    structured_html = render_note("Dylan and Matt driving trucks", has_structured=True)
    if '<details class="raw-source raw-roster-note">' not in structured_html:
        raise AssertionError("Structured roster note lost its Raw roster note control.")
    informational_html = render_note("Please call the office before leaving.")
    if '<details class="raw-source raw-roster-note">' not in informational_html:
        raise AssertionError("Unstructured informational note lost its Raw roster note control.")
    no_note_html = render_note("")
    if "Raw roster note" in no_note_html:
        raise AssertionError("Raw roster note control rendered without source note text.")
    race_day_html = render_note("Race briefing is at 09:00.", race_day=True)
    if "Race Day" not in race_day_html or '<details class="raw-source raw-roster-note">' not in race_day_html:
        raise AssertionError("Race Day source note lost its independent Raw roster note control.")


def render_timesheet_template() -> None:
    env = Environment(loader=FileSystemLoader(ROOT / "app" / "templates"))
    env.filters.update(datetime=datetime_filter, time=str, day_short=str, hours=str, urlencode=quote_plus)
    env.globals["theme_values"] = THEME_VALUES
    html = env.get_template("timesheet.html").render(
        request={}, notice=None, current_user=None, month_year=2026, month_number=2,
        summary={
            "period_label": "24 Jan-06 Feb 2026", "total": 8.5,
            "days": [{
                "iso": "2026-02-06", "date_label": "Fri 06 Feb", "total": 8.5,
                "locations": "Te Rapa", "notes": [],
                "shifts": [{
                    "track_label": "Te Rapa", "time_range": "09:30–18:30", "display_hours_label": "9h",
                    "display_window": {
                        "source": "roster", "start_label": "09:30",
                        "end_label": "18:30", "hours_label": "9h",
                    },
                    "timing_math": {"race_day": {
                        "available": True, "complete": True, "start_label": "08:30",
                        "end_label": "18:45", "hours_label": "10h 15m",
                        "lines": SIMPLIFIED_CALCULATION_LINES,
                        "formula": "Using the scheduled last race from Love Racing. Office / Clow Place to Te Rapa and return.",
                    }},
                }, {
                    "track_label": "Unconfigured Track", "time_range": "09:30-", "display_hours_label": "Incomplete",
                    "display_window": {
                        "source": "roster", "start_label": "09:30",
                        "end_label": "", "hours_label": "Incomplete",
                    },
                    "timing_math": {"race_day": {
                        "available": True, "complete": False, "start_label": "09:30",
                        "end_label": "", "hours_label": "Incomplete",
                        "warning": "Return travel not configured",
                        "lines": [
                            {"label": "Start · Office / Clow Place", "value": "09:30"},
                            {"label": "Return travel not configured", "value": "Incomplete"},
                            {"label": "Finish · Office / Clow Place", "value": "Not calculated"},
                        ],
                        "formula": "Return travel is not configured, so the finish and total are incomplete.",
                    }},
                }],
                "holiday": {"is_public_holiday": True, "names": ["Waitangi Day"], "name": "Waitangi Day", "aria_label": "Public holiday: Waitangi Day"},
            }],
        },
    )
    if 'aria-label="Public holiday: Waitangi Day"' not in html:
        raise AssertionError("Timesheet row did not render the holiday marker.")
    if 'class="timesheet-date-heading"' not in html:
        raise AssertionError("Timesheet holiday marker is not in reserved date-heading layout space.")
    if "Start · Office / Clow Place" not in html or "Finish · Office / Clow Place" not in html:
        raise AssertionError("Timesheet template did not render the shared simplified wording.")
    if "Calculated operational timing: Using the scheduled last race from Love Racing" not in html:
        raise AssertionError("Timesheet template did not render cached Love Racing calculation evidence.")
    if "Deputy roster: 09:30–18:30 = 9h" not in html:
        raise AssertionError("Timesheet calculation replaced the Deputy roster window with an estimate.")
    if "Deputy roster start" not in html:
        raise AssertionError("Timesheet template hid an operational timing discrepancy.")
    if "Return travel not configured" not in html:
        raise AssertionError("Timesheet template hid a missing-route warning.")
    if any(label in html for label in ("Start origin evidence", "Finish destination evidence", "roster base timing")):
        raise AssertionError("Timesheet template rendered internal timeline evidence labels.")


def check_holiday_marker_css() -> None:
    css = (ROOT / "app" / "static" / "style.css").read_text(encoding="utf-8")
    if ".calendar-date-heading" not in css or "calc(100vw - 24px)" not in css:
        raise AssertionError("Holiday marker layout or narrow-viewport popover constraint is missing.")
    if ".day-cell > .holiday-marker" in css or ".timesheet-day > .holiday-marker" in css:
        raise AssertionError("Holiday markers must not be absolutely positioned over date headings.")


def check_roster_note_css() -> None:
    css = (ROOT / "app" / "static" / "style.css").read_text(encoding="utf-8")
    required = (
        ".roster-note-panel",
        "background: var(--surface-raised);",
        "border: 1px solid var(--line);",
        ".roster-note-lines",
        ".raw-roster-note-content",
        "background: var(--surface-soft);",
        "white-space: pre-wrap;",
        "overflow-wrap: anywhere;",
    )
    if any(value not in css for value in required):
        raise AssertionError("Roster-note panel lost its theme-safe evidence presentation rules.")
    note_css = css[css.index(".roster-note-panel"):css.index(".race-day-panel", css.index(".roster-note-panel"))]
    if "border-bottom" in note_css or ".roster-lines" in note_css:
        raise AssertionError("Roster-note panel must not inherit row dividers or generic roster-list styling.")
    if ".roster-lines li {\n  padding: 8px 0;\n  border-bottom: 1px solid var(--line);" not in css:
        raise AssertionError("Generic roster-list separators were unexpectedly changed.")


def check_product_branding() -> None:
    template_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "app" / "templates").glob("*.html")
    )
    if "Deputy Roster View" in template_text or "Re-Deputy Roster View" in template_text:
        raise AssertionError("A retired product name remains in the main UI templates.")
    if "Re-Deputy" not in template_text:
        raise AssertionError("The Re-Deputy product name is missing from the UI templates.")
    if "Deputy roster" not in template_text:
        raise AssertionError("Deputy is no longer identified as the external roster source.")


if __name__ == "__main__":
    render_day_template()
    render_month_template()
    render_roster_note_template()
    render_timesheet_template()
    check_holiday_marker_css()
    check_roster_note_css()
    check_product_branding()
    print("template smoke render ok")
