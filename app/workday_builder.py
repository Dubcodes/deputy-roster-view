from __future__ import annotations

import re


WORKDAY_TYPES = (
    ("race_day", "Race day"),
    ("office_day", "Office day"),
    ("travel_day", "Travel day"),
    ("training_day", "Training day"),
    ("other_work", "Other work day"),
)
WORKDAY_TYPE_LABELS = dict(WORKDAY_TYPES)

TRANSPORT_MODES = (
    ("unassigned", "No transport assigned yet"),
    ("self_travel", "Making own way"),
    ("not_required", "No transport required"),
    ("vehicle", "Crew vehicle"),
    ("custom", "Custom transport"),
)
TRANSPORT_LABELS = dict(TRANSPORT_MODES)

BUILT_IN_ROLES = (
    ("side1", "Side 1", ("Side One",)),
    ("side2", "Side 2", ("Side Two",)),
    ("start", "Start", ("Start Cam",)),
    ("headon", "Head On", ("Head-on",)),
    ("back", "Back", ("Back Straight",)),
    ("back2", "Back2", ("Back 2",)),
    ("turn", "Turn", ()),
    ("ivbp", "IV / BP", ("IV", "BP", "Back Parade")),
    ("rts", "RTS", ()),
    ("gimbal", "Gimbal", ("Gimble",)),
    ("gimbalassist", "Gimbal Assist", ("Gimble Assist",)),
    ("steadi", "Steadi", ("Steady",)),
    ("steadiassist", "Steadi Assist", ("Steady Assist",)),
    ("director", "Director", ("DIR",)),
    ("sound", "Sound", ()),
    ("soundvt", "Sound/VT", ("Sound VT", "SVT")),
    ("vt", "VT", ()),
    ("ccu1", "CCU1", ("CCU 1",)),
    ("ccu2", "CCU2", ("CCU 2",)),
    ("fm", "FM", ("Floor Manager",)),
    ("eng", "ENG", ("Engineer",)),
    ("generalcrew", "General crew", ()),
)

WORKDAY_PRESETS = {
    "thoroughbred_standard": {
        "label": "Thoroughbred standard",
        "day_type": "race_day",
        "roles": [
            "Side 1", "Side 2", "Start", "Head On", "Back", "Turn", "RTS",
            "Director", "Sound", "VT", "CCU1", "CCU2", "FM", "ENG",
        ],
    },
    "harness_standard": {
        "label": "Harness standard",
        "day_type": "race_day",
        "roles": ["Side 1", "Side 2", "Head On", "Back", "Director", "Sound/VT", "CCU1", "CCU2", "FM", "ENG"],
    },
    "trials": {
        "label": "Trials",
        "day_type": "race_day",
        "roles": ["Side 1", "Side 2", "Head On", "Back", "Director", "Sound/VT", "ENG"],
    },
    "office_day": {
        "label": "Office day",
        "day_type": "office_day",
        "roles": [],
    },
    "blank": {
        "label": "Blank / custom",
        "day_type": "other_work",
        "roles": [],
    },
}


def normalise_role_key(value: object) -> str:
    text = str(value or "").strip().lower()
    return re.sub(r"[^a-z0-9]+", "", text)


def canonical_role_key(value: object, aliases: dict[str, str] | None = None) -> str:
    key = normalise_role_key(value)
    if aliases and key in aliases:
        return aliases[key]
    for built_in_key, label, built_in_aliases in BUILT_IN_ROLES:
        if key in {normalise_role_key(label), *(normalise_role_key(alias) for alias in built_in_aliases)}:
            return built_in_key
    return key


def transport_display(
    mode: object,
    vehicle_label: object = "",
    custom_text: object = "",
) -> str:
    mode_text = str(mode or "unassigned").strip()
    if mode_text == "vehicle":
        return str(vehicle_label or "").strip() or "TBC"
    if mode_text == "custom":
        return str(custom_text or "").strip() or "Custom transport"
    return TRANSPORT_LABELS.get(mode_text, "No transport assigned yet")


def legacy_transport_mode(vehicle_label: object) -> str:
    return "vehicle" if str(vehicle_label or "").strip() else "unassigned"
