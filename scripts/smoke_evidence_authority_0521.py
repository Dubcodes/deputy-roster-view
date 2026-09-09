from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    sys.path.insert(0, str(ROOT))
    temp_dir = Path(tempfile.mkdtemp(prefix="deputy-evidence-authority-"))
    os.environ.update(
        DATA_DIR=str(temp_dir),
        DB_PATH=str(temp_dir / "authority.sqlite3"),
        APP_SECRET_KEY="authority-smoke",
        TZ="Pacific/Auckland",
    )

    from app.config import get_settings
    from app.database import (
        fetch_deputy_event_changes_for_date,
        fetch_deputy_schedule_for_date,
        init_db,
        save_deputy_web_schedule,
    )
    from app.main import reconcile_personal_assignment_evidence, schedule_people

    init_db()
    now = datetime.now(get_settings().timezone)
    with sqlite3.connect(get_settings().db_path) as conn:
        for user_id, name in ((1, "Observer One"), (2, "Observer Two")):
            conn.execute(
                """INSERT INTO app_users
                   (id,deputy_email,display_name,pin_hash,deputy_web_url,is_admin,is_active,created_at,updated_at)
                   VALUES (?,?,?,?,?,0,1,?,?)""",
                (user_id, f"observer{user_id}@example.test", name, "x", "https://example.test", now.isoformat(), now.isoformat()),
            )
        conn.commit()

    location_id = 640
    areas = {
        "vt": {"id": 776, "name": "VT", "locationId": location_id, "rosterSortOrder": 9},
        "soundvt": {"id": 777, "name": "Sound/VT", "locationId": location_id, "rosterSortOrder": 8},
        "sound": {"id": 778, "name": "Sound", "locationId": location_id, "rosterSortOrder": 8},
        "director": {"id": 779, "name": "Director", "locationId": location_id, "rosterSortOrder": 7},
    }

    def date_at(days: int) -> str:
        return (now + timedelta(days=days)).date().isoformat()

    def stamp(minutes: int) -> str:
        return (now + timedelta(minutes=minutes)).isoformat()

    def row(
        source_id: int,
        date_text: str,
        role: str,
        employee_id: int | None,
        employee_name: str,
        *,
        is_open: bool = False,
    ) -> dict[str, object]:
        area = areas[role]
        return {
            "id": source_id,
            "area": area["id"],
            "areaName": area["name"],
            "areaLocationId": location_id,
            "location": location_id,
            "locationName": "Evidence Park",
            "employee": employee_id,
            "employeeName": employee_name,
            "start": f"{date_text}T09:00:00+12:00",
            "end": f"{date_text}T18:00:00+12:00",
            "duration": 32400,
            "isOpen": is_open,
            "isPublished": True,
        }

    def capture(
        date_text: str,
        captured_at: str,
        rows: list[dict[str, object]],
        *,
        status: str = "complete",
        owner_id: int = 1,
    ) -> dict[str, int]:
        return save_deputy_web_schedule(
            {
                "captured_at": captured_at,
                "areas": list(areas.values()),
                "locations": [{"id": location_id, "name": "Evidence Park"}],
                "extracted_shifts": [],
                "own_roster_coverage": [],
                "extracted_schedule_shifts": rows,
                "native_schedule_shift_ids": [int(item["id"]) for item in rows],
                "direct_schedule_shift_ids": [],
                "schedule_coverage": [],
                "management_schedule_coverage": [
                    {"start_date": date_text, "end_date": date_text, "status": status, "row_count": len(rows)}
                ],
            },
            owner_user_id=owner_id,
        )

    def direct_capture(
        date_text: str,
        captured_at: str,
        rows: list[dict[str, object]],
        *,
        exact: bool,
        retry_status: str | None = None,
        owner_id: int = 1,
    ) -> dict[str, int]:
        return save_deputy_web_schedule(
            {
                "captured_at": captured_at,
                "areas": list(areas.values()),
                "locations": [{"id": location_id, "name": "Evidence Park"}],
                "extracted_shifts": [],
                "own_roster_coverage": [],
                "extracted_schedule_shifts": rows,
                "native_schedule_shift_ids": [],
                "direct_schedule_shift_ids": [int(item["id"]) for item in rows],
                "schedule_coverage": [{
                    "start_date": date_text,
                    "end_date": date_text,
                    "mode": "selected" if exact else "all",
                    "location_ids": [location_id] if exact else [],
                }],
                "event_retry_coverage": ([{
                    "date": date_text, "location_id": location_id, "status": retry_status,
                }] if retry_status else []),
                "management_schedule_coverage": [],
            },
            owner_user_id=owner_id,
        )

    def people(date_text: str) -> list[dict[str, object]]:
        return schedule_people(fetch_deputy_schedule_for_date(date_text, [location_id]), include_placeholders=False)

    def observation_active(
        source_id: int,
        owner_id: int = 1,
        source: str = "native_get_rosters",
    ) -> int | None:
        with sqlite3.connect(get_settings().db_path) as conn:
            value = conn.execute(
                "SELECT active FROM deputy_schedule_observations WHERE source_shift_id=? AND observer_key=?",
                (source_id, f"user:{owner_id}:{source}"),
            ).fetchone()
        return int(value[0]) if value else None

    vt_a = {"employee_id": 59, "employee_name": "Darryl Cribb"}
    vt_b = {"employee_id": 77, "employee_name": "James Topping"}

    # A partial response is positive evidence for B, not negative evidence for A.
    partial_date = date_at(90)
    partial_a = row(61001, partial_date, "vt", vt_a["employee_id"], vt_a["employee_name"])
    partial_b = row(61002, partial_date, "vt", vt_b["employee_id"], vt_b["employee_name"])
    capture(partial_date, stamp(1), [partial_a, partial_b])
    history_before_partial = len(fetch_deputy_event_changes_for_date(partial_date, [location_id]))
    capture(partial_date, stamp(2), [partial_b], status="partial")
    if observation_active(61001) != 1:
        raise AssertionError("Partial native B-only capture retired prior VT A evidence.")
    partial_people = people(partial_date)
    if len(partial_people) != 1 or partial_people[0]["position_label"] != "VT" or not partial_people[0].get("conflict_warning"):
        raise AssertionError(f"Partial B-only result was not conservative: {partial_people!r}")
    if len(fetch_deputy_event_changes_for_date(partial_date, [location_id])) != history_before_partial:
        raise AssertionError("Partial native omission manufactured VT removal history.")

    # A complete response covering the same event may retire the absent VT.
    complete_date = date_at(91)
    complete_a = row(61101, complete_date, "vt", vt_a["employee_id"], vt_a["employee_name"])
    complete_b = row(61102, complete_date, "vt", vt_b["employee_id"], vt_b["employee_name"])
    capture(complete_date, stamp(3), [complete_a, complete_b])
    capture(complete_date, stamp(4), [complete_b])
    if observation_active(61101) not in (None, 0):
        raise AssertionError("Complete native B-only capture did not retire prior VT A evidence.")
    if [(item["position_label"], item["employee_name"]) for item in people(complete_date)] != [("VT", "James Topping")]:
        raise AssertionError(f"Complete B-only result was not singular: {people(complete_date)!r}")
    complete_history = fetch_deputy_event_changes_for_date(complete_date, [location_id])
    if len(complete_history) != 1 or complete_history[0]["change_type"] != "replacement":
        raise AssertionError(f"Complete VT replacement did not create one real history story: {[dict(item) for item in complete_history]!r}")

    # Positive A+B co-observation is valid even when the wider response is partial.
    partial_pair_date = date_at(92)
    pair_a = row(61201, partial_pair_date, "vt", vt_a["employee_id"], vt_a["employee_name"])
    pair_b = row(61202, partial_pair_date, "vt", vt_b["employee_id"], vt_b["employee_name"])
    capture(partial_pair_date, stamp(5), [pair_a, pair_b], status="partial")
    if [(item["position_label"], item["employee_name"]) for item in people(partial_pair_date)] != [
        ("VT 1", "Darryl Cribb"), ("VT 2", "James Topping"),
    ]:
        raise AssertionError(f"Partial positive A+B capture lost proven concurrency: {people(partial_pair_date)!r}")

    # Failed native requests carry no negative authority and create no history.
    failed_date = date_at(93)
    failed_a = row(61301, failed_date, "vt", vt_a["employee_id"], vt_a["employee_name"])
    failed_b = row(61302, failed_date, "vt", vt_b["employee_id"], vt_b["employee_name"])
    capture(failed_date, stamp(6), [failed_a, failed_b])
    failed_history = len(fetch_deputy_event_changes_for_date(failed_date, [location_id]))
    capture(failed_date, stamp(7), [], status="partial")
    if observation_active(61301) != 1 or observation_active(61302) != 1:
        raise AssertionError("Failed native request retired prior positive VT evidence.")
    if len(fetch_deputy_event_changes_for_date(failed_date, [location_id])) != failed_history:
        raise AssertionError("Failed native request manufactured Change History.")

    # A complete combined state retires the now-absent separate VT. Without
    # that convergence, stale VT evidence keeps the combined row falsely split.
    sound_vt_date = date_at(94)
    combined = row(61401, sound_vt_date, "soundvt", 81, "Ryan Beaven")
    separate_vt = row(61402, sound_vt_date, "vt", 82, "Jayden Smith")
    capture(sound_vt_date, stamp(8), [combined, separate_vt])
    if [(item["position_label"], item["employee_name"]) for item in people(sound_vt_date)] != [
        ("Sound", "Ryan Beaven"), ("VT", "Jayden Smith"),
    ]:
        raise AssertionError(f"Initial split Sound/VT state was not interpreted correctly: {people(sound_vt_date)!r}")
    capture(sound_vt_date, stamp(9), [combined])
    if [(item["position_label"], item["employee_name"]) for item in people(sound_vt_date)] != [
        ("Sound/VT", "Ryan Beaven"),
    ]:
        raise AssertionError(f"Complete combined state retained stale separate VT: {people(sound_vt_date)!r}")
    sound_history = fetch_deputy_event_changes_for_date(sound_vt_date, [location_id])
    if len(sound_history) != 1 or sound_history[0]["change_type"] != "merge":
        raise AssertionError(f"Sound/VT convergence did not create one semantic merge: {[dict(item) for item in sound_history]!r}")

    # A later observer touching an unchanged shared source row must not erase
    # Observer One's still-valid A+B same-capture proof.
    shared_touch_date = date_at(95)
    shared_a = row(61501, shared_touch_date, "vt", vt_a["employee_id"], vt_a["employee_name"])
    shared_b = row(61502, shared_touch_date, "vt", vt_b["employee_id"], vt_b["employee_name"])
    capture(shared_touch_date, stamp(10), [shared_a, shared_b], owner_id=1)
    capture(shared_touch_date, stamp(11), [shared_a], status="partial", owner_id=2)
    if [(item["position_label"], item["employee_name"]) for item in people(shared_touch_date)] != [
        ("VT 1", "Darryl Cribb"), ("VT 2", "James Topping"),
    ]:
        raise AssertionError(f"Shared-row touch erased older observer concurrency proof: {people(shared_touch_date)!r}")

    # The older proof must not be reused after assignment-relevant row content
    # changes under the same Deputy source-shift ID.
    mutated_date = date_at(96)
    mutated_a = row(61601, mutated_date, "vt", vt_a["employee_id"], vt_a["employee_name"])
    mutated_b = row(61602, mutated_date, "vt", vt_b["employee_id"], vt_b["employee_name"])
    capture(mutated_date, stamp(12), [mutated_a, mutated_b], owner_id=1)
    changed_a = row(61601, mutated_date, "vt", 83, "Replacement Operator")
    capture(mutated_date, stamp(13), [changed_a], status="partial", owner_id=2)
    mutated_people = people(mutated_date)
    if any(str(item.get("position_label") or "").startswith("VT ") for item in mutated_people):
        raise AssertionError(f"Older observation proof was reused for mutated assignment content: {mutated_people!r}")

    # Ordinary roles remain single-assignee. A named positive replacement is
    # self-proving even in a partial response; a vacancy needs complete event
    # coverage before it can displace a named assignment.
    director_date = date_at(97)
    luke = row(61701, director_date, "director", 91, "Luke Houghton")
    grant = row(61702, director_date, "director", 92, "Grant Woolston")
    capture(director_date, stamp(14), [luke])
    capture(director_date, stamp(15), [grant], status="partial")
    if [(item["position_label"], item["employee_name"]) for item in people(director_date)] != [
        ("Director", "Grant Woolston"),
    ]:
        raise AssertionError(f"Ordinary positive replacement was weakened by partial coverage: {people(director_date)!r}")

    open_date = date_at(98)
    open_luke = row(61801, open_date, "director", 91, "Luke Houghton")
    open_slot = row(61802, open_date, "director", None, "", is_open=True)
    capture(open_date, stamp(16), [open_luke])
    capture(open_date, stamp(17), [open_slot])
    if [(item["position_label"], item["employee_name"]) for item in people(open_date)] != [
        ("Director", "Open shift"),
    ]:
        raise AssertionError(f"Complete named-to-Open transition retained stale named evidence: {people(open_date)!r}")

    filled_date = date_at(99)
    vacant_director = row(61901, filled_date, "director", None, "", is_open=True)
    filled_director = row(61902, filled_date, "director", 92, "Grant Woolston")
    capture(filled_date, stamp(18), [vacant_director])
    capture(filled_date, stamp(19), [filled_director], status="partial")
    if observation_active(61901) not in (None, 0):
        raise AssertionError("Positive named Director did not retire the same observer's obsolete Open slot.")
    if [(item["position_label"], item["employee_name"]) for item in people(filled_date)] != [
        ("Director", "Grant Woolston"),
    ]:
        raise AssertionError(f"Open-to-named Director transition was not singular: {people(filled_date)!r}")

    omitted_date = date_at(100)
    omitted_luke = row(62001, omitted_date, "director", 91, "Luke Houghton")
    capture(omitted_date, stamp(20), [omitted_luke])
    capture(omitted_date, stamp(21), [], status="partial")
    if [(item["position_label"], item["employee_name"]) for item in people(omitted_date)] != [
        ("Director", "Luke Houghton"),
    ]:
        raise AssertionError("Partial native omission removed an ordinary named assignment.")

    director_conflict_date = date_at(101)
    observer_luke = row(62101, director_conflict_date, "director", 91, "Luke Houghton")
    observer_grant = row(62102, director_conflict_date, "director", 92, "Grant Woolston")
    capture(director_conflict_date, stamp(22), [observer_luke], owner_id=1)
    capture(director_conflict_date, stamp(23), [observer_grant], status="partial", owner_id=2)
    director_conflict = people(director_conflict_date)
    if len(director_conflict) != 1 or not director_conflict[0].get("conflict_warning"):
        raise AssertionError(f"Cross-observer Director disagreement was not conservative: {director_conflict!r}")
    if observation_active(62101, owner_id=1) != 1 or observation_active(62102, owner_id=2) != 1:
        raise AssertionError("One observer retired another observer's Director evidence.")

    # VT transition matrix: positive pair growth, stable repeat, and complete
    # contraction all retain the maximum-two cardinality contract.
    growing_date = date_at(102)
    growing_a = row(62201, growing_date, "vt", vt_a["employee_id"], vt_a["employee_name"])
    growing_b = row(62202, growing_date, "vt", vt_b["employee_id"], vt_b["employee_name"])
    capture(growing_date, stamp(24), [growing_a])
    capture(growing_date, stamp(25), [growing_a, growing_b])
    expected_pair = [("VT 1", "Darryl Cribb"), ("VT 2", "James Topping")]
    if [(item["position_label"], item["employee_name"]) for item in people(growing_date)] != expected_pair:
        raise AssertionError(f"Complete A to A+B did not establish VT concurrency: {people(growing_date)!r}")
    history_before_repeat = len(fetch_deputy_event_changes_for_date(growing_date, [location_id]))
    capture(growing_date, stamp(26), [growing_a, growing_b])
    if [(item["position_label"], item["employee_name"]) for item in people(growing_date)] != expected_pair:
        raise AssertionError("Unchanged A+B capture was not stable.")
    if len(fetch_deputy_event_changes_for_date(growing_date, [location_id])) != history_before_repeat:
        raise AssertionError("Unchanged A+B capture created false Change History.")
    capture(growing_date, stamp(27), [growing_a])
    if [(item["position_label"], item["employee_name"]) for item in people(growing_date)] != [
        ("VT", "Darryl Cribb"),
    ]:
        raise AssertionError(f"Complete A+B to A did not retire B: {people(growing_date)!r}")

    # Sound-family transition matrix. Partial apparent convergence preserves
    # the split; positive or complete states can establish actual transitions.
    partial_audio_date = date_at(103)
    partial_combined = row(62301, partial_audio_date, "soundvt", 81, "Ryan Beaven")
    partial_separate = row(62302, partial_audio_date, "vt", 82, "Jayden Smith")
    capture(partial_audio_date, stamp(28), [partial_combined, partial_separate])
    partial_audio_history = len(fetch_deputy_event_changes_for_date(partial_audio_date, [location_id]))
    capture(partial_audio_date, stamp(29), [partial_combined], status="partial")
    if [(item["position_label"], item["employee_name"]) for item in people(partial_audio_date)] != [
        ("Sound", "Ryan Beaven"), ("VT", "Jayden Smith"),
    ]:
        raise AssertionError(f"Partial combined-looking response manufactured a merge: {people(partial_audio_date)!r}")
    if len(fetch_deputy_event_changes_for_date(partial_audio_date, [location_id])) != partial_audio_history:
        raise AssertionError("Partial combined-looking response manufactured merge history.")

    split_date = date_at(104)
    split_combined = row(62401, split_date, "soundvt", 81, "Ryan Beaven")
    split_vt = row(62402, split_date, "vt", 82, "Jayden Smith")
    capture(split_date, stamp(30), [split_combined])
    capture(split_date, stamp(31), [split_combined, split_vt])
    if [(item["position_label"], item["employee_name"]) for item in people(split_date)] != [
        ("Sound", "Ryan Beaven"), ("VT", "Jayden Smith"),
    ]:
        raise AssertionError(f"Positive combined-to-split state was lost: {people(split_date)!r}")
    split_history = fetch_deputy_event_changes_for_date(split_date, [location_id])
    if len(split_history) != 1 or split_history[0]["change_type"] != "split":
        raise AssertionError(f"Combined-to-split did not create one semantic history story: {[dict(item) for item in split_history]!r}")

    explicit_date = date_at(105)
    old_combined = row(62501, explicit_date, "soundvt", 81, "Ryan Beaven")
    explicit_sound = row(62502, explicit_date, "sound", 81, "Ryan Beaven")
    explicit_vt = row(62503, explicit_date, "vt", 82, "Jayden Smith")
    capture(explicit_date, stamp(32), [old_combined])
    capture(explicit_date, stamp(33), [explicit_sound, explicit_vt])
    if observation_active(62501) not in (None, 0):
        raise AssertionError("Complete explicit Sound+VT state retained obsolete combined evidence.")
    if [(item["position_label"], item["employee_name"]) for item in people(explicit_date)] != [
        ("Sound", "Ryan Beaven"), ("VT", "Jayden Smith"),
    ]:
        raise AssertionError(f"Explicit Sound+VT state was not preserved: {people(explicit_date)!r}")

    audio_conflict_date = date_at(106)
    conflict_combined = row(62601, audio_conflict_date, "soundvt", 81, "Ryan Beaven")
    conflict_vt = row(62602, audio_conflict_date, "vt", 82, "Jayden Smith")
    capture(audio_conflict_date, stamp(34), [conflict_combined], owner_id=1)
    capture(audio_conflict_date, stamp(35), [conflict_combined, conflict_vt], status="partial", owner_id=2)
    if observation_active(62601, owner_id=1) != 1:
        raise AssertionError("Observer Two's audio state retired Observer One's combined evidence.")
    if [(item["position_label"], item["employee_name"]) for item in people(audio_conflict_date)] != [
        ("Sound", "Ryan Beaven"), ("VT", "Jayden Smith"),
    ]:
        raise AssertionError(f"Cross-observer positively proven split was not represented: {people(audio_conflict_date)!r}")

    # Direct-search absence is destructive only for an exact selected complete
    # event scope. A broad response with an incomplete selected retry is not
    # permission to collapse a valid maximum-two VT state.
    direct_partial_date = date_at(107)
    direct_a = row(62701, direct_partial_date, "vt", vt_a["employee_id"], vt_a["employee_name"])
    direct_b = row(62702, direct_partial_date, "vt", vt_b["employee_id"], vt_b["employee_name"])
    direct_capture(direct_partial_date, stamp(36), [direct_a, direct_b], exact=True)
    direct_capture(direct_partial_date, stamp(37), [direct_b], exact=False, retry_status="partial")
    if observation_active(62701, source="direct_schedule") != 1:
        raise AssertionError("Broad partial direct response retired prior VT evidence.")

    direct_complete_date = date_at(108)
    exact_a = row(62801, direct_complete_date, "vt", vt_a["employee_id"], vt_a["employee_name"])
    exact_b = row(62802, direct_complete_date, "vt", vt_b["employee_id"], vt_b["employee_name"])
    direct_capture(direct_complete_date, stamp(38), [exact_a, exact_b], exact=True)
    direct_capture(direct_complete_date, stamp(39), [exact_b], exact=True)
    if observation_active(62801, source="direct_schedule") not in (None, 0):
        raise AssertionError("Exact selected complete direct response did not retire absent VT evidence.")

    # Authenticated personal evidence is authoritative for that person. Because
    # VT permits two people it preserves A beside shared B, while an ordinary
    # Director disagreement remains a conflict and does not replace Grant.
    personal_vt_date = date_at(109)
    personal_b = row(62901, personal_vt_date, "vt", vt_b["employee_id"], vt_b["employee_name"])
    capture(personal_vt_date, stamp(40), [personal_b], status="partial")
    personal_vt_people = people(personal_vt_date)
    reconcile_personal_assignment_evidence(personal_vt_people, [{
        "position_label": "VT",
        "employee_name": "Darryl Cribb",
        "deputy_employee_id": 59,
        "canonical_person_id": 59,
        "evidence_type": "production_position",
        "status": "confirmed",
    }])
    if {item["employee_name"] for item in personal_vt_people} != {"Darryl Cribb", "James Topping"}:
        raise AssertionError(f"Authenticated VT A disappeared beside shared partial VT B: {personal_vt_people!r}")

    shared_director = [{
        "position_label": "Director", "employee_name": "Grant Woolston",
        "employee_id": 92, "placeholder": False, "sort_order": 7,
    }]
    reconcile_personal_assignment_evidence(shared_director, [{
        "position_label": "Director",
        "employee_name": "Luke Houghton",
        "deputy_employee_id": 91,
        "canonical_person_id": 91,
        "evidence_type": "production_position",
        "status": "possibly_missing",
    }])
    if len(shared_director) != 1 or shared_director[0]["employee_name"] != "Grant Woolston" or not shared_director[0].get("conflict_warning"):
        raise AssertionError(f"Stale personal Director evidence overrode proven shared Grant: {shared_director!r}")

    print("evidence authority 0.5.21 smoke ok")


if __name__ == "__main__":
    main()
