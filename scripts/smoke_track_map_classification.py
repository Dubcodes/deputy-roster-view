from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def fake_png(marker: bytes) -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR"
        + (640).to_bytes(4, "big") + (480).to_bytes(4, "big") + marker
    )


def main() -> None:
    sys.path.insert(0, str(ROOT))
    temp_dir = Path(tempfile.mkdtemp(prefix="deputy-map-classification-"))
    os.environ.update(
        DATA_DIR=str(temp_dir),
        DB_PATH=str(temp_dir / "maps.sqlite3"),
        APP_SECRET_KEY="map-classification-smoke",
        SIGNUP_ENABLED="true",
        COOKIE_SECURE="false",
    )

    from fastapi.testclient import TestClient

    from app.database import (
        get_connection,
        get_app_user_by_email,
        get_track_map,
        init_db,
        list_track_map_migration_warnings,
        set_track_map_manual_override,
        upsert_track_map,
    )
    from app.main import app, track_map_admin_data, track_maps_for_day
    from app.track_maps import (
        classify_track_map_location,
        migrate_existing_track_map_aliases,
        reset_manual_track_map,
        save_manual_track_map,
        track_map_storage_key,
    )

    init_db()
    excluded = (
        "(A) Office", "Abandoned", "MEWP Training", "Northern Ops",
        "Northern Ops - Contractors", "VEH", "Travel then Overnighter",
        "Annual Leave", "Central Ops - Contractors", "Training (At track)",
    )
    for label in excluded:
        if classify_track_map_location(label)["classification"] != "excluded":
            raise AssertionError(f"Operational location was not excluded: {label}")

    aliases = {
        "TRIALS - Te Rapa": ("terapa", "Te Rapa"),
        "Trials Avondale": ("avondale", "Avondale"),
        "T-Trials Cambridge": ("cambridge", "Cambridge Synthetic"),
        "TRIALS - Waipa": ("waipa", "Waipa"),
        "  trials   -   ROTORUA  ": ("arawapark", "Rotorua"),
        "Trials / Pukekohe": ("pukekohepark", "Pukekohe"),
        "Trials_Taupo": ("taupo", "Taupo"),
        "T-TRIALS - Matamata": ("matamata", "Matamata"),
    }
    for label, expected in aliases.items():
        result = classify_track_map_location(label)
        actual = (result["canonical_key"], result["canonical_label"])
        if result["classification"] != "alias" or actual != expected:
            raise AssertionError(f"Trial alias {label!r} resolved to {result!r}")
    if track_map_storage_key("TRIALS - Cambridge") != track_map_storage_key("Cambridge Synthetic"):
        raise AssertionError("Cambridge trials must share Cambridge Synthetic's map key.")

    map_dir = temp_dir / "track_maps"
    map_dir.mkdir(parents=True, exist_ok=True)
    auto_bytes = b"automatic-te-rapa"
    (map_dir / "terapa.jpg").write_bytes(auto_bytes)
    upsert_track_map(
        track_key="terapa", track_label="Te Rapa", course_label="Te Rapa",
        course_url="https://example.invalid/te-rapa", image_url="https://example.invalid/te-rapa.jpg",
        file_name="terapa.jpg", content_type="image/jpeg", image_hash="auto",
        status="ok", checked_at="2026-07-22T09:00:00+12:00", updated_at="2026-07-22T09:00:00+12:00",
    )

    legacy_bytes = fake_png(b"legacy-alias")
    legacy_name = "manual-trialsterapa.png"
    (map_dir / legacy_name).write_bytes(legacy_bytes)
    set_track_map_manual_override(
        track_key="trialsterapa", track_label="TRIALS - Te Rapa", file_name=legacy_name,
        content_type="image/png", image_hash="legacy-alias", image_width=640,
        image_height=480, byte_size=len(legacy_bytes), updated_at="2026-07-22T09:10:00+12:00",
    )
    migration = migrate_existing_track_map_aliases()
    canonical = get_track_map("terapa")
    alias = get_track_map("trialsterapa")
    if migration["adopted"] != 1 or canonical["manual_image_hash"] != "legacy-alias":
        raise AssertionError(f"Legacy alias override was not adopted: {migration!r} {dict(canonical)!r}")
    if alias is not None and alias["manual_file_name"]:
        raise AssertionError("Alias retained a second active manual image record.")
    if not reset_manual_track_map("TRIALS - Te Rapa") or get_track_map("terapa")["manual_file_name"]:
        raise AssertionError("Reset through an alias did not reset the canonical venue.")

    canonical_bytes = fake_png(b"canonical")
    saved = save_manual_track_map("Te Rapa", canonical_bytes)
    conflict_bytes = fake_png(b"conflicting-alias")
    conflict_name = "manual-trials-te-rapa-conflict.png"
    (map_dir / conflict_name).write_bytes(conflict_bytes)
    set_track_map_manual_override(
        track_key="trialsterapa", track_label="Trials - Te Rapa", file_name=conflict_name,
        content_type="image/png", image_hash="conflicting-alias", image_width=640,
        image_height=480, byte_size=len(conflict_bytes), updated_at="2026-07-22T09:20:00+12:00",
    )
    conflict = migrate_existing_track_map_aliases()
    if conflict["conflicts"] != 1 or get_track_map("terapa")["manual_image_hash"] != saved["image_hash"]:
        raise AssertionError("Canonical manual override did not win an alias conflict.")
    if not (map_dir / conflict_name).is_file():
        raise AssertionError("Conflicting alias file was silently deleted.")
    if not any(row["warning_type"] == "conflicting_alias_upload" for row in list_track_map_migration_warnings()):
        raise AssertionError("Conflicting alias upload was not recorded for admin review.")

    observed = [
        *excluded, "Alexandra Park", "Manukau", "Te Rapa", "TRIALS - Te Rapa",
        "Trials Avondale", "T-Trials Cambridge", "TRIALS - Waipa", "Mystery Downs",
    ]
    with get_connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO crew_pools (name, created_at, updated_at) VALUES ('Northern Crew', '', '')"
        )
        for label in observed:
            conn.execute(
                """INSERT OR IGNORE INTO crew_known_locations
                   (crew_name, location_key, display_name, first_seen_at, last_seen_at)
                   VALUES ('Northern Crew', ?, ?, '', '')""",
                ("observed-" + str(abs(hash(label))), label),
            )

    admin_data = track_map_admin_data()
    venue_labels = [str(row["track_label"]) for row in admin_data["venues"]]
    if "Alexandra Park" not in venue_labels or "Manukau" not in venue_labels:
        raise AssertionError("Valid non-thoroughbred venues were excluded without automatic maps.")
    if venue_labels.count("Te Rapa") != 1 or "TRIALS - Te Rapa" in venue_labels:
        raise AssertionError(f"Trial aliases created duplicate venue rows: {venue_labels!r}")
    unclassified = [str(row["location_label"]) for row in admin_data["unclassified"]]
    if "Mystery Downs" not in unclassified:
        raise AssertionError("An uncertain location was silently discarded instead of left for admin review.")
    if set(excluded) & set(venue_labels + unclassified):
        raise AssertionError("Operational locations leaked into map management.")

    trial_maps = track_maps_for_day([{"track_label": "TRIALS - Te Rapa"}], [])
    if len(trial_maps) != 1 or trial_maps[0]["track_key"] != "terapa":
        raise AssertionError(f"Trial day did not use the canonical map: {trial_maps!r}")
    if track_maps_for_day([{"track_label": "(A) Office"}], []):
        raise AssertionError("A non-track day displayed a track map.")

    client = TestClient(app)
    signup = client.post(
        "/signup",
        data={
            "deputy_web_url": "https://example.invalid/#/", "deputy_email": "admin@example.com",
            "deputy_password": "password", "pin": "1234", "pin_confirm": "1234",
            "next_url": "/admin",
        },
        follow_redirects=False,
    )
    if signup.status_code != 303:
        raise AssertionError(f"Admin signup failed: {signup.status_code}")
    admin_user = get_app_user_by_email("admin@example.com")
    with get_connection() as conn:
        conn.executemany(
            """INSERT INTO shifts (
                   source_uid, title, start_at, end_at, date, raw_hours, paid_hours,
                   deleted_from_source, owner_user_id, source_payload
               ) VALUES (?, ?, ?, ?, ?, 8, 8, 0, ?, '{}')""",
            [
                (
                    "map:trial-day", "[T-TRIALS - Te Rapa] Director",
                    "2026-07-23T09:00:00+12:00", "2026-07-23T17:00:00+12:00",
                    "2026-07-23", int(admin_user["id"]),
                ),
                (
                    "map:office-day", "[(A) Office] Training",
                    "2026-07-24T09:00:00+12:00", "2026-07-24T17:00:00+12:00",
                    "2026-07-24", int(admin_user["id"]),
                ),
            ],
        )
    page = client.get("/admin")
    map_section = page.text.split("<strong>Track maps</strong>", 1)[1].split("Travel-route matrix", 1)[0]
    if map_section.count('/admin/track-maps/terapa/upload') != 1:
        raise AssertionError("Admin did not render the canonical Te Rapa venue exactly once.")
    if "Also used for: TRIALS - Te Rapa" not in map_section:
        raise AssertionError("Admin did not show compact trial-alias metadata.")
    for label in excluded:
        if label in map_section:
            raise AssertionError(f"Admin rendered excluded location {label!r} in Track maps.")
    if "Unclassified locations" not in map_section or "Mystery Downs" not in map_section:
        raise AssertionError("Admin did not render the compact unclassified-location controls.")
    if f'/admin/track-map-migration-files/' not in map_section:
        raise AssertionError("Admin did not provide access to a retained alias upload.")
    warning_id = int(list_track_map_migration_warnings()[0]["id"])
    retained = client.get(f"/admin/track-map-migration-files/{warning_id}")
    if retained.status_code != 200:
        raise AssertionError("Admin could not download a retained alias upload.")
    trial_day = client.get("/day/2026-07-23")
    if 'src="/track-map/terapa?' not in trial_day.text:
        raise AssertionError("Rendered trial day did not use the canonical Te Rapa image.")
    office_day = client.get("/day/2026-07-24")
    if "track-map-section" in office_day.text:
        raise AssertionError("Rendered non-track day displayed a track-map panel.")

    print("track-map classification smoke ok")


if __name__ == "__main__":
    main()
