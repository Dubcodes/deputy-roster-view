from __future__ import annotations

import re


TRAVEL_PARTICIPANT_COHORT_KEYS = {
    "travel",
    "overnighter",
    "travelthenovernighter",
    "outofregion",
}
TRAVEL_LOCATION_ALIAS_KEYS = {"travel", "ttravel"}


def travel_label_key(value: object) -> str:
    value = str(value or "").strip().lower().replace("&", "and")
    return re.sub(r"[^a-z0-9]+", "", value)


def is_travel_participant_cohort(value: object) -> bool:
    return travel_label_key(value) in TRAVEL_PARTICIPANT_COHORT_KEYS


def travel_family_locations_match(
    left_location: object,
    left_area: object,
    right_location: object,
    right_area: object,
) -> bool:
    """Match Travel/T-Travel only within the proven participant-area family."""
    left_key = travel_label_key(left_location)
    right_key = travel_label_key(right_location)
    if not left_key or not right_key:
        return False
    return (
        is_travel_participant_cohort(left_area)
        and is_travel_participant_cohort(right_area)
        and left_key in TRAVEL_LOCATION_ALIAS_KEYS
        and right_key in TRAVEL_LOCATION_ALIAS_KEYS
    )
