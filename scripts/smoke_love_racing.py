from __future__ import annotations

from datetime import date

from app.love_racing import parse_love_racing_events, parse_nztr_calendar_fragments
from app.track_maps import parse_track_map_image_url, track_map_course_key


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

    track_html = """
    <img src="/Common/Image.ashx?version=3.191&amp;w=400&amp;p=/OnHorseFiles/Racecourses/Tracks/2D%20with%20updated%20Logo/Te-Aroha_new.jpg"
         alt="Track - 2D">
    """
    image_url = parse_track_map_image_url(
        track_html,
        "https://loveracing.nz/RaceInfo/Clubs-And-Courses/34/35/Club.aspx",
    )
    if "loveracing.nz/Common/Image.ashx" not in image_url or "w=1200" not in image_url:
        raise AssertionError(f"Track-map image was not normalised: {image_url!r}")
    if "Te-Aroha_new.jpg" not in image_url:
        raise AssertionError(f"Track-map source path was lost: {image_url!r}")
    aliases = {
        "Te Aroha": "tearoha",
        "Rotorua": "arawapark",
        "Cambridge Synthetic": "cambridge",
        "Cambridge Harness": "",
    }
    for label, expected_key in aliases.items():
        actual_key = track_map_course_key(label)
        if actual_key != expected_key:
            raise AssertionError(f"Track-map alias failed for {label!r}: {actual_key!r}")
    print("Love Racing/NZTR planning smoke ok")


if __name__ == "__main__":
    main()
