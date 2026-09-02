from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from .travel_cohorts import is_travel_participant_cohort


OPERATIONAL_CONTEXT_KEYS = frozenset({
    "", "accommodation", "clowplace", "manager", "maintenance",
    "mewptraining", "northern", "northernopscontractors", "office",
    "shift", "training", "web",
})
GENERIC_VEHICLE_KEYS = frozenset({"vehicle", "vehicles"})
NAMED_VEHICLE_KEYS = frozenset({"ob", "tender", "transit"})


def evidence_label_key(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().casefold())


def is_vehicle_context(value: object) -> bool:
    key = evidence_label_key(value)
    return bool(
        key in GENERIC_VEHICLE_KEYS
        or key in NAMED_VEHICLE_KEYS
        or re.fullmatch(r"\d{3,4}", key)
        or re.fullmatch(r"rav\w*", key)
        or re.fullmatch(r"rp\d+", key)
    )


@dataclass(frozen=True)
class DeputyEvidenceClassification:
    raw_label: str
    role_key: str
    role_label: str
    evidence_type: str
    production_position: bool
    participant_evidence: bool
    cohort_type: str
    vehicle_context: bool


def classify_deputy_evidence(
    value: object,
    *,
    production_keys: Iterable[str] = (),
    production_aliases: dict[str, tuple[str, str]] | None = None,
) -> DeputyEvidenceClassification:
    """Classify normalized Deputy evidence without using display as retention."""
    raw_label = re.sub(r"\s+", " ", str(value or "").strip())
    key = evidence_label_key(raw_label)
    aliases = production_aliases or {}
    known_production_keys = set(production_keys)
    if is_travel_participant_cohort(raw_label):
        return DeputyEvidenceClassification(
            raw_label, key or "travel", raw_label or "Travel",
            "participant_cohort", False, True, "travel", False,
        )
    if is_vehicle_context(raw_label):
        return DeputyEvidenceClassification(
            raw_label, key or "vehicle", raw_label or "Vehicle",
            "vehicle_context", False, False, "", True,
        )
    if key in OPERATIONAL_CONTEXT_KEYS:
        return DeputyEvidenceClassification(
            raw_label, key or "unknown", raw_label or "Unknown area",
            "operational_context", False, False, "", False,
        )
    if key in aliases:
        canonical_key, canonical_label = aliases[key]
        return DeputyEvidenceClassification(
            raw_label, canonical_key, canonical_label,
            "production_position", True, True, "", False,
        )
    if key in known_production_keys:
        return DeputyEvidenceClassification(
            raw_label, key, raw_label or "Position",
            "production_position", True, True, "", False,
        )
    return DeputyEvidenceClassification(
        raw_label, key or "unknown", raw_label or "Unknown area",
        "unknown", False, False, "", False,
    )
