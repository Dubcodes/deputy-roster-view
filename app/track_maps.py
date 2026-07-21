from __future__ import annotations

import hashlib
import os
import re
import struct
from datetime import datetime, timedelta
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qsl, quote, unquote, urljoin, urlsplit, urlunsplit

import requests

from .config import Settings, get_settings
from .database import (
    calendar_location_key,
    clear_track_map_manual_override,
    get_app_setting,
    get_track_map,
    list_known_racecourse_names,
    set_track_map_manual_override,
    update_app_settings,
    upsert_track_map,
)


LOVE_RACING_ORIGIN = "https://loveracing.nz"
MAX_MANUAL_MAP_BYTES = 15 * 1024 * 1024
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
        self.candidates: list[str] = []
        self._links: list[str] = []

    def _add(self, value: str) -> None:
        clean = unescape(str(value or "").strip())
        if clean and clean not in self.candidates:
            self.candidates.append(clean)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        values = {key.lower(): str(value or "") for key, value in attrs}
        if tag == "a":
            self._links.append(values.get("href", ""))
            return
        if tag == "meta" and values.get("property", "").lower() == "og:image":
            self._add(values.get("content", ""))
            return
        if tag != "img":
            return
        joined = " ".join(values.values()).lower()
        credible = (
            "track - 2d" in values.get("alt", "").lower()
            or "racecourses/tracks" in joined
            or "onhorsefiles" in joined and "track" in joined
        )
        if not credible:
            return
        for attribute in ("src", "data-src", "data-original"):
            self._add(values.get(attribute, ""))
        for srcset_attribute in ("srcset", "data-srcset"):
            for item in values.get(srcset_attribute, "").split(","):
                self._add(item.strip().split(" ", 1)[0])
        if self._links:
            self._add(self._links[-1])

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._links:
            self._links.pop()


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


def track_map_storage_key(track_label: object) -> str:
    return track_map_course_key(track_label) or calendar_location_key(track_label)


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


def _official_image_url(value: str, course_url: str) -> str:
    absolute = _direct_image_url(urljoin(course_url, value))
    host = (urlsplit(absolute).hostname or "").lower()
    if host != "loveracing.nz" and not host.endswith(".loveracing.nz"):
        return ""
    path = unquote(urlsplit(absolute).path).lower()
    if not re.search(r"\.(?:jpe?g|png|webp)$", path):
        return ""
    return absolute


def parse_track_map_image_candidates(html: str, course_url: str) -> list[str]:
    parser = _TrackMapImageParser()
    parser.feed(html)
    candidates: list[str] = []
    for raw_value in parser.candidates:
        candidate = _official_image_url(raw_value, course_url)
        if candidate and candidate not in candidates:
            candidates.append(candidate)
    return candidates


def parse_track_map_image_url(html: str, course_url: str) -> str:
    candidates = parse_track_map_image_candidates(html, course_url)
    return candidates[0] if candidates else ""


def _discover_image_candidates(session: requests.Session, course_url: str, fallback_path: str) -> list[str]:
    fallback = _official_image_url(fallback_path, course_url)
    try:
        response = session.get(course_url, headers=PAGE_HEADERS, timeout=20, allow_redirects=True)
    except requests.RequestException:
        return [fallback] if fallback else []
    if response.status_code != 200 or not response.text.strip():
        return [fallback] if fallback else []
    candidates = parse_track_map_image_candidates(response.text, course_url)
    if fallback and fallback not in candidates:
        candidates.append(fallback)
    return candidates


def image_dimensions(content: bytes, content_type: str = "") -> tuple[int, int]:
    if content.startswith(b"\x89PNG\r\n\x1a\n") and len(content) >= 24:
        return struct.unpack(">II", content[16:24])
    if content[:2] == b"\xff\xd8":
        offset = 2
        while offset + 9 < len(content):
            if content[offset] != 0xFF:
                offset += 1
                continue
            marker = content[offset + 1]
            offset += 2
            if marker in {0xD8, 0xD9}:
                continue
            if offset + 2 > len(content):
                break
            length = int.from_bytes(content[offset:offset + 2], "big")
            if length < 2 or offset + length > len(content):
                break
            if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
                return (
                    int.from_bytes(content[offset + 5:offset + 7], "big"),
                    int.from_bytes(content[offset + 3:offset + 5], "big"),
                )
            offset += length
    if content.startswith(b"RIFF") and content[8:12] == b"WEBP" and len(content) >= 30:
        if content[12:16] == b"VP8X":
            width = 1 + int.from_bytes(content[24:27], "little")
            height = 1 + int.from_bytes(content[27:30], "little")
            return width, height
    return 0, 0


def image_content_type(content: bytes) -> tuple[str, str]:
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", ".png"
    if content[:2] == b"\xff\xd8":
        return "image/jpeg", ".jpg"
    if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        return "image/webp", ".webp"
    return "", ""


def effective_track_map_file(row: object) -> dict[str, object]:
    values = dict(row) if row is not None else {}
    manual_file_name = str(values.get("manual_file_name") or "").strip()
    manual = bool(manual_file_name)
    prefix = "manual_" if manual else ""
    return {
        "file_name": manual_file_name if manual else str(values.get("file_name") or "").strip(),
        "content_type": str(values.get(f"{prefix}content_type") or "image/jpeg"),
        "image_hash": str(values.get(f"{prefix}image_hash") or ""),
        "image_width": int(values.get(f"{prefix}image_width") or 0),
        "image_height": int(values.get(f"{prefix}image_height") or 0),
        "byte_size": int(values.get(f"{prefix}byte_size") or 0),
        "updated_at": str(values.get("manual_updated_at" if manual else "updated_at") or ""),
        "is_manual": manual,
    }


def save_manual_track_map(
    track_label: str,
    content: bytes,
    settings: Settings | None = None,
) -> dict[str, object]:
    settings = settings or get_settings()
    label = str(track_label or "").strip()
    track_key = track_map_storage_key(label)
    if not label or not track_key:
        raise ValueError("Track name is missing.")
    if not content:
        raise ValueError("Choose an image to upload.")
    if len(content) > MAX_MANUAL_MAP_BYTES:
        raise ValueError("Track map images must be 15 MB or smaller.")
    content_type, extension = image_content_type(content)
    width, height = image_dimensions(content, content_type)
    if not content_type or not width or not height:
        raise ValueError("Upload a valid JPEG, PNG, or WebP image.")
    map_dir = Path(settings.data_dir) / "track_maps"
    map_dir.mkdir(parents=True, exist_ok=True)
    file_name = f"manual-{track_key}{extension}"
    destination = map_dir / file_name
    previous = get_track_map(track_key)
    previous_manual = str(previous["manual_file_name"] or "") if previous is not None else ""
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_bytes(content)
    os.replace(temporary, destination)
    if previous_manual and previous_manual != file_name:
        previous_path = map_dir / Path(previous_manual).name
        if previous_path.parent == map_dir and previous_path.is_file():
            previous_path.unlink()
    updated_at = datetime.now(settings.timezone).isoformat(timespec="seconds")
    image_hash = hashlib.sha256(content).hexdigest()
    set_track_map_manual_override(
        track_key=track_key, track_label=label, file_name=file_name,
        content_type=content_type, image_hash=image_hash,
        image_width=width, image_height=height, byte_size=len(content),
        updated_at=updated_at,
    )
    return {
        "track_key": track_key, "track_label": label, "file_name": file_name,
        "content_type": content_type, "image_width": width, "image_height": height,
        "byte_size": len(content), "image_hash": image_hash, "updated_at": updated_at,
    }


def reset_manual_track_map(track_key: str, settings: Settings | None = None) -> bool:
    settings = settings or get_settings()
    key = track_map_storage_key(track_key)
    if not key:
        return False
    file_name = clear_track_map_manual_override(key)
    if not file_name:
        return False
    map_dir = (Path(settings.data_dir) / "track_maps").resolve()
    image_path = (map_dir / Path(file_name).name).resolve()
    if image_path.parent == map_dir and image_path.is_file():
        image_path.unlink()
    return True


def _candidate_matches_course(image_url: str, course: dict[str, object]) -> bool:
    filename_key = calendar_location_key(Path(unquote(urlsplit(image_url).path)).stem)
    expected = {
        calendar_location_key(str(course.get("course_label") or "")),
        calendar_location_key(str(course.get("label") or "")),
        *(calendar_location_key(alias) for alias in course.get("aliases", ())),
    }
    expected.discard("")
    return any(key in filename_key or filename_key in key for key in expected)


def _previous_map_score(previous: object, map_dir: Path) -> tuple[int, int, int]:
    if previous is None:
        return (0, 0, 0)
    width = int(previous["image_width"] or 0)
    height = int(previous["image_height"] or 0)
    byte_size = int(previous["byte_size"] or 0)
    file_path = map_dir / Path(str(previous["file_name"] or "")).name
    if file_path.is_file():
        content = file_path.read_bytes()
        if not width or not height:
            width, height = image_dimensions(content, str(previous["content_type"] or ""))
        byte_size = byte_size or len(content)
    return width * height, min(width, height), byte_size


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
    upgraded = 0
    unchanged = 0
    unavailable = 0
    failed = 0
    errors: list[str] = []
    results: list[dict[str, object]] = []

    with requests.Session() as session:
        for course_key, course in targets:
            course_url = urljoin(LOVE_RACING_ORIGIN, str(course["course_path"]))
            previous = get_track_map(course_key)
            image_urls = _discover_image_candidates(session, course_url, str(course["image_path"]))
            headers = dict(IMAGE_HEADERS)
            headers["Referer"] = course_url
            candidates: list[dict[str, object]] = []
            candidate_errors: list[str] = []
            for image_url in image_urls:
                try:
                    response = session.get(image_url, headers=headers, timeout=30, allow_redirects=True)
                    response.raise_for_status()
                except requests.RequestException as exc:
                    candidate_errors.append(f"{type(exc).__name__}: {exc}")
                    continue
                content_type = str(response.headers.get("content-type") or "").split(";", 1)[0].lower()
                if content_type not in {"image/jpeg", "image/png", "image/webp"} or not response.content:
                    candidate_errors.append(f"unexpected {content_type or 'content type'}")
                    continue
                width, height = image_dimensions(response.content, content_type)
                if width < 200 or height < 200 or not _candidate_matches_course(image_url, course):
                    candidate_errors.append(f"invalid {width}x{height} candidate")
                    continue
                candidates.append({
                    "url": image_url, "content": response.content, "content_type": content_type,
                    "width": width, "height": height, "byte_size": len(response.content),
                    "hash": hashlib.sha256(response.content).hexdigest(),
                })
            if not candidates:
                outcome = "failed" if candidate_errors else "unavailable"
                failed += outcome == "failed"
                unavailable += outcome == "unavailable"
                reason = candidate_errors[0] if candidate_errors else "no official image candidate"
                errors.append(f"{course['label']}: {reason}")
                results.append({"track_key": course_key, "label": course["label"], "result": outcome})
                if previous is None:
                    upsert_track_map(
                        track_key=course_key, track_label=str(course["label"]),
                        course_label=str(course["course_label"]), course_url=course_url,
                        image_url=image_urls[0] if image_urls else "", file_name="", content_type="",
                        image_hash="", status="error", checked_at=checked_at, updated_at="",
                        candidate_count=len(image_urls), refresh_result=outcome,
                    )
                continue
            best = max(candidates, key=lambda item: (int(item["width"]) * int(item["height"]), min(int(item["width"]), int(item["height"])), int(item["byte_size"])))
            image_url = str(best["url"])
            content_type = str(best["content_type"])
            extension = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}[content_type]
            file_name = f"{course_key}{extension}"
            destination = map_dir / file_name
            image_hash = str(best["hash"])
            existing_hash = hashlib.sha256(destination.read_bytes()).hexdigest() if destination.exists() else ""
            previous_score = _previous_map_score(previous, map_dir)
            best_score = (int(best["width"]) * int(best["height"]), min(int(best["width"]), int(best["height"])), int(best["byte_size"]))
            if previous is not None and previous_score > best_score:
                unchanged += 1
                results.append({"track_key": course_key, "label": course["label"], "result": "unchanged", "width": previous_score[0]})
                continue
            if existing_hash == image_hash:
                unchanged += 1
                updated_at = datetime.fromtimestamp(destination.stat().st_mtime, settings.timezone).isoformat(timespec="seconds")
                outcome = "unchanged"
            else:
                temporary = destination.with_suffix(destination.suffix + ".tmp")
                temporary.write_bytes(bytes(best["content"]))
                os.replace(temporary, destination)
                downloaded += 1
                if previous is not None and best_score > previous_score:
                    upgraded += 1
                    outcome = "upgraded"
                else:
                    outcome = "downloaded"
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
                image_width=int(best["width"]), image_height=int(best["height"]),
                byte_size=int(best["byte_size"]), selected_source_url=image_url,
                candidate_count=len(image_urls), refresh_result=outcome,
            )
            results.append({
                "track_key": course_key, "label": course["label"], "result": outcome,
                "width": int(best["width"]), "height": int(best["height"]),
                "byte_size": int(best["byte_size"]), "source_url": image_url,
            })
            previous_name = Path(str(previous["file_name"] or "")).name if previous is not None else ""
            if previous_name and previous_name != file_name:
                previous_path = map_dir / previous_name
                if previous_path.parent == map_dir and previous_path.is_file():
                    previous_path.unlink()
    return {
        "status": "ok" if downloaded or unchanged else "empty",
        "checked": len(targets),
        "downloaded": downloaded,
        "upgraded": upgraded,
        "unchanged": unchanged,
        "unavailable": unavailable,
        "failed": failed,
        "errors": errors,
        "results": results,
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
