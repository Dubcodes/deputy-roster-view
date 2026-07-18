from __future__ import annotations

import hashlib
import os
from datetime import datetime, timedelta
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qsl, quote, unquote, urljoin, urlsplit, urlunsplit

import requests

from .config import Settings, get_settings
from .database import (
    calendar_location_key,
    get_app_setting,
    get_track_map,
    list_known_racecourse_names,
    update_app_settings,
    upsert_track_map,
)


LOVE_RACING_ORIGIN = "https://loveracing.nz"
IMAGE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
}
PAGE_HEADERS = {
    "User-Agent": IMAGE_HEADERS["User-Agent"],
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-NZ,en;q=0.9",
}


COURSE_CATALOG = {
    "arawapark": {
        "label": "Rotorua",
        "course_label": "Arawa Park",
        "course_path": "/RaceInfo/Clubs-And-Courses/1/Racecourse.aspx",
        "image_path": "/OnHorseFiles/Racecourses/Tracks/2D%20with%20updated%20Logo/Arawa%20Park%20copy.jpg",
        "aliases": ("Rotorua", "Arawa Park"),
    },
    "avondale": {
        "label": "Avondale",
        "course_label": "Avondale",
        "course_path": "/RaceInfo/Clubs-And-Courses/4/Racecourse.aspx",
        "image_path": "/OnHorseFiles/Racecourses/Tracks/2D%20with%20updated%20Logo/Avondale%202.jpg",
        "aliases": ("Avondale",),
    },
    "cambridge": {
        "label": "Cambridge Synthetic",
        "course_label": "Cambridge",
        "course_path": "/RaceInfo/Clubs-And-Courses/7/Racecourse.aspx",
        "image_path": "/OnHorseFiles/Racecourses/Tracks/Cambridge-synthethic_2D.jpg",
        "aliases": ("Cambridge Synthetic", "T-Cambridge"),
    },
    "ellerslie": {
        "label": "Ellerslie",
        "course_label": "Ellerslie",
        "course_path": "/RaceInfo/Clubs-And-Courses/9/Racecourse.aspx",
        "image_path": "/OnHorseFiles/Racecourses/Tracks/Ellerslie_2025.png",
        "aliases": ("Ellerslie",),
    },
    "matamata": {
        "label": "Matamata",
        "course_label": "Matamata",
        "course_path": "/RaceInfo/Clubs-And-Courses/18/Racecourse.aspx",
        "image_path": "/OnHorseFiles/Racecourses/Tracks/2D%20with%20updated%20Logo/Matamata%20copy.jpg",
        "aliases": ("Matamata",),
    },
    "pukekohepark": {
        "label": "Pukekohe",
        "course_label": "Pukekohe Park",
        "course_path": "/RaceInfo/Clubs-And-Courses/26/Racecourse.aspx",
        "image_path": "/OnHorseFiles/Racecourses/Tracks/2D%20with%20updated%20Logo/Pukekohe%20copy.jpg",
        "aliases": ("Pukekohe", "Pukekohe Park"),
    },
    "ruakaka": {
        "label": "Ruakaka",
        "course_label": "Ruakaka",
        "course_path": "/RaceInfo/Clubs-And-Courses/32/Racecourse.aspx",
        "image_path": "/OnHorseFiles/Racecourses/Tracks/2D%20with%20updated%20Logo/Ruakaka%20copy.jpg",
        "aliases": ("Ruakaka",),
    },
    "taupo": {
        "label": "Taupo",
        "course_label": "Taupo",
        "course_path": "/RaceInfo/Clubs-And-Courses/34/Racecourse.aspx",
        "image_path": "/OnHorseFiles/Racecourses/Tracks/2D%20with%20updated%20Logo/Taupo%20copy.jpg",
        "aliases": ("Taupo",),
    },
    "tauranga": {
        "label": "Tauranga",
        "course_label": "Tauranga",
        "course_path": "/RaceInfo/Clubs-And-Courses/41/Racecourse.aspx",
        "image_path": "/OnHorseFiles/Racecourses/Tracks/Tauranga-2D_new.jpg",
        "aliases": ("Tauranga",),
    },
    "tearoha": {
        "label": "Te Aroha",
        "course_label": "Te Aroha",
        "course_path": "/RaceInfo/Clubs-And-Courses/35/Racecourse.aspx",
        "image_path": "/OnHorseFiles/Racecourses/Tracks/2D%20with%20updated%20Logo/Te-Aroha_new.jpg",
        "aliases": ("Te Aroha",),
    },
    "terapa": {
        "label": "Te Rapa",
        "course_label": "Te Rapa",
        "course_path": "/RaceInfo/Clubs-And-Courses/38/Racecourse.aspx",
        "image_path": "/OnHorseFiles/Racecourses/Tracks/2D%20with%20updated%20Logo/Te%20Rapa%20copy.jpg",
        "aliases": ("Te Rapa",),
    },
}


class _TrackMapImageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.image_src = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "img" or self.image_src:
            return
        values = {key.lower(): str(value or "") for key, value in attrs}
        if values.get("alt", "").strip().lower() == "track - 2d":
            self.image_src = unescape(values.get("src", "").strip())


def track_map_course_key(track_label: object) -> str:
    candidate = calendar_location_key(track_label)
    if not candidate:
        return ""
    if candidate in COURSE_CATALOG:
        return candidate
    for course_key, course in COURSE_CATALOG.items():
        if candidate in {calendar_location_key(alias) for alias in course["aliases"]}:
            return course_key
    return ""


def _direct_image_url(value: str) -> str:
    absolute = urljoin(LOVE_RACING_ORIGIN, value)
    parts = urlsplit(absolute)
    if not parts.path.lower().endswith("/common/image.ashx"):
        return absolute
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    image_path = unquote(unescape(str(query.get("p") or "").strip()))
    if not image_path:
        return absolute
    return urlunsplit((parts.scheme, parts.netloc, quote(image_path, safe="/"), "", ""))
def parse_track_map_image_url(html: str, course_url: str) -> str:
    parser = _TrackMapImageParser()
    parser.feed(html)
    if not parser.image_src:
        return ""
    return _direct_image_url(urljoin(course_url, parser.image_src))


def _discover_image_url(session: requests.Session, course_url: str, fallback_path: str) -> str:
    try:
        response = session.get(course_url, headers=PAGE_HEADERS, timeout=20, allow_redirects=True)
    except requests.RequestException:
        return _direct_image_url(fallback_path)
    if response.status_code != 200 or not response.text.strip():
        return _direct_image_url(fallback_path)
    discovered = parse_track_map_image_url(response.text, course_url)
    return discovered or _direct_image_url(fallback_path)


def refresh_track_maps(settings: Settings | None = None) -> dict[str, object]:
    settings = settings or get_settings()
    now = datetime.now(settings.timezone)
    checked_at = now.isoformat(timespec="seconds")
    known_keys = {
        calendar_location_key(value)
        for value in list_known_racecourse_names(include_fallback=False)
    }
    targets = [
        (course_key, course)
        for course_key, course in COURSE_CATALOG.items()
        if known_keys & {calendar_location_key(alias) for alias in course["aliases"]}
    ]
    map_dir = Path(settings.data_dir) / "track_maps"
    map_dir.mkdir(parents=True, exist_ok=True)
    downloaded = 0
    unchanged = 0
    failed = 0
    errors: list[str] = []

    with requests.Session() as session:
        for course_key, course in targets:
            course_url = urljoin(LOVE_RACING_ORIGIN, str(course["course_path"]))
            image_url = _discover_image_url(session, course_url, str(course["image_path"]))
            previous = get_track_map(course_key)
            headers = dict(IMAGE_HEADERS)
            headers["Referer"] = course_url
            try:
                response = session.get(image_url, headers=headers, timeout=30, allow_redirects=True)
                response.raise_for_status()
            except requests.RequestException as exc:
                failed += 1
                reason = f"{type(exc).__name__}: {exc}"
                errors.append(f"{course['label']}: {reason}")
                if previous is None:
                    upsert_track_map(
                        track_key=course_key,
                        track_label=str(course["label"]),
                        course_label=str(course["course_label"]),
                        course_url=course_url,
                        image_url=image_url,
                        file_name="",
                        content_type="",
                        image_hash="",
                        status="error",
                        checked_at=checked_at,
                        updated_at="",
                    )
                continue
            content_type = str(response.headers.get("content-type") or "").split(";", 1)[0].lower()
            if content_type not in {"image/jpeg", "image/png", "image/webp"} or not response.content:
                failed += 1
                reason = f"unexpected content type {content_type or 'unknown'} ({len(response.content)} bytes)"
                errors.append(f"{course['label']}: {reason}")
                if previous is None:
                    upsert_track_map(
                        track_key=course_key,
                        track_label=str(course["label"]),
                        course_label=str(course["course_label"]),
                        course_url=course_url,
                        image_url=image_url,
                        file_name="",
                        content_type=content_type,
                        image_hash="",
                        status="error",
                        checked_at=checked_at,
                        updated_at="",
                    )
                continue
            extension = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}[content_type]
            file_name = f"{course_key}{extension}"
            destination = map_dir / file_name
            image_hash = hashlib.sha256(response.content).hexdigest()
            existing_hash = hashlib.sha256(destination.read_bytes()).hexdigest() if destination.exists() else ""
            if existing_hash == image_hash:
                unchanged += 1
                updated_at = datetime.fromtimestamp(destination.stat().st_mtime, settings.timezone).isoformat(
                    timespec="seconds"
                )
            else:
                temporary = destination.with_suffix(destination.suffix + ".tmp")
                temporary.write_bytes(response.content)
                os.replace(temporary, destination)
                downloaded += 1
                updated_at = checked_at
            upsert_track_map(
                track_key=course_key,
                track_label=str(course["label"]),
                course_label=str(course["course_label"]),
                course_url=course_url,
                image_url=image_url,
                file_name=file_name,
                content_type=content_type,
                image_hash=image_hash,
                status="ok",
                checked_at=checked_at,
                updated_at=updated_at,
            )
            previous_name = Path(str(previous["file_name"] or "")).name if previous is not None else ""
            if previous_name and previous_name != file_name:
                previous_path = map_dir / previous_name
                if previous_path.parent == map_dir and previous_path.is_file():
                    previous_path.unlink()
    return {
        "status": "ok" if downloaded or unchanged else "empty",
        "checked": len(targets),
        "downloaded": downloaded,
        "unchanged": unchanged,
        "failed": failed,
        "errors": errors,
    }


def refresh_track_maps_if_due(settings: Settings | None = None, minimum_days: int = 28) -> dict[str, object]:
    settings = settings or get_settings()
    now = datetime.now(settings.timezone)
    try:
        last_checked = datetime.fromisoformat(get_app_setting("track_maps_last_checked_at", ""))
    except (TypeError, ValueError):
        last_checked = None
    if last_checked and now - last_checked < timedelta(days=max(1, minimum_days)):
        return {
            "status": "skipped",
            "checked": 0,
            "downloaded": 0,
            "unchanged": 0,
            "failed": 0,
        }
    result = refresh_track_maps(settings)
    if result.get("status") == "ok":
        update_app_settings({"track_maps_last_checked_at": now.isoformat(timespec="seconds")})
    return result
