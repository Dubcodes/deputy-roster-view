from __future__ import annotations

from datetime import date
from pathlib import Path
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
        month_year=2026,
        month_number=6,
        deputy_schedule_changed=False,
        deputy_schedule_people=[],
        deputy_schedule_label="Deputy Schedule",
        shifts=[
            {
                "id": 1,
                "deleted_from_source": 0,
                "colour_style": "",
                "time_range": "08:30-17:45",
                "role_chain_label": "Sound VT",
                "role_full_label": "Sound VT",
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


if __name__ == "__main__":
    render_day_template()
    print("template smoke render ok")
