from __future__ import annotations

from datetime import date

from app.love_racing import parse_love_racing_events, parse_nztr_calendar_fragments


def main() -> None:
    pages = [
        [
            (105.9, 102.9, "Monday, 27 July 2026", 13.6),
            (1122.2, 113.9, "Whangarei RC(x8) 12:00pm", 7.7),
            (904.0, 123.5, "Auckland TC(x9) 5:00pm", 7.7),
            (206.9, 253.9, "3-Aug", 13.6),
            (904.0, 265.0, "Waikato TR@CAMB SYNTH(x8) 12:00pm", 7.7),
            (1122.2, 265.0, "Waikato TR@TE RAPA(x8) 12:00pm", 7.7),
        ],
        [
            (31.0, 69.1, "28-Dec", 13.6),
            (31.0, 182.4, "4-Jan", 13.6),
            (1122.2, 194.2, "Waikato TR@TE RAPA(x8) 12:00pm", 7.7),
        ],
    ]
    events = parse_nztr_calendar_fragments(pages)
    dates = [event["DateISO"] for event in events]
    if dates != ["2026-08-01", "2026-08-07", "2026-08-08", "2027-01-09"]:
        raise AssertionError(f"Unexpected positioned calendar dates: {dates!r}")

    meetings = parse_love_racing_events(events, ["Ruakaka", "Te Rapa"], today=date(2026, 7, 27))
    summary = [(meeting["date"], meeting["racecourse"]) for meeting in meetings]
    expected = [
        ("2026-08-01", "Ruakaka"),
        ("2026-08-08", "Te Rapa"),
        ("2027-01-09", "Te Rapa"),
    ]
    if summary != expected:
        raise AssertionError(f"Known-location filtering failed: {summary!r}")
    print("Love Racing/NZTR planning smoke ok")


if __name__ == "__main__":
    main()
