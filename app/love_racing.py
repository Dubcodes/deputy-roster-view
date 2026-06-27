from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date, datetime
from html import unescape

import requests

from .database import calendar_location_key


LOVE_RACING_URL = "https://loveracing.nz/RaceInfo.aspx#bm-meeting-calendar"
LOVE_RACING_FETCH_URL = "https://loveracing.nz/RaceInfo.aspx"
MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}
DATE_RE = re.compile(
    r"\b(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+(\d{1,2})\s+"
    r"(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?|tember)?|Oct(?:ober)?|"
    r"Nov(?:ember)?|Dec(?:ember)?)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class LoveRacingResult:
    meetings: list[dict[str, object]]
    fetched_rows: int
    matched_rows: int
    message: str


def fetch_love_racing_meetings(known_locations: list[str], today: date | None = None) -> LoveRacingResult:
    today = today or date.today()
    response = requests.get(
        LOVE_RACING_FETCH_URL,
        timeout=20,
        headers={
            "User-Agent": "DeputyRosterView/0.5 (+private planning calendar)",
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    response.raise_for_status()
    text = response.text
    meetings = parse_love_racing_meetings(text, known_locations=known_locations, today=today)
    return LoveRacingResult(
        meetings=meetings,
        fetched_rows=len(_candidate_rows(_page_text(text))),
        matched_rows=len(meetings),
        message=f"Love Racing scan found {len(meetings)} known future race days.",
    )


def parse_love_racing_meetings(html: str, known_locations: list[str], today: date | None = None) -> list[dict[str, object]]:
    today = today or date.today()
    text = _page_text(html)
    aliases = _location_aliases(known_locations)
    meetings: list[dict[str, object]] = []
    seen: set[tuple[str, str, str]] = set()
    for row_text in _candidate_rows(text):
        meeting_date = _meeting_date_from_row(row_text, today.year)
        if meeting_date is None or meeting_date < today:
            continue
        match = _match_known_location(row_text, aliases)
        if match is None:
            continue
        racecourse, racecourse_key = match
        club_name = _club_name_from_row(row_text, racecourse)
        dedupe_key = (meeting_date.isoformat(), racecourse_key, calendar_location_key(club_name))
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        source_hash = hashlib.sha256("|".join(dedupe_key + (row_text,)).encode("utf-8")).hexdigest()
        meetings.append(
            {
                "date": meeting_date.isoformat(),
                "racecourse": racecourse,
                "racecourse_key": racecourse_key,
                "club_name": club_name,
                "source_url": LOVE_RACING_URL,
                "source_hash": source_hash,
                "raw_text": row_text,
            }
        )
    return meetings


def _page_text(html: str) -> str:
    cleaned = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    cleaned = re.sub(r"(?is)<br\s*/?>", "\n", cleaned)
    cleaned = re.sub(r"(?is)</(?:tr|p|div|li|h[1-6])>", "\n", cleaned)
    cleaned = re.sub(r"(?is)<[^>]+>", " ", cleaned)
    cleaned = unescape(cleaned)
    cleaned = re.sub(r"[ \t\r\f\v]+", " ", cleaned)
    cleaned = re.sub(r"\n\s+", "\n", cleaned)
    return cleaned


def _candidate_rows(text: str) -> list[str]:
    matches = list(DATE_RE.finditer(text))
    rows = []
    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else min(len(text), match.end() + 260)
        row_text = re.sub(r"\s+", " ", text[start:end]).strip()
        if row_text:
            rows.append(row_text)
    return rows


def _meeting_date_from_row(row_text: str, year: int) -> date | None:
    match = DATE_RE.search(row_text)
    if not match:
        return None
    month = MONTHS.get(match.group(3).lower())
    if not month:
        return None
    try:
        return date(year, month, int(match.group(2)))
    except ValueError:
        return None


def _location_aliases(known_locations: list[str]) -> list[tuple[str, str, str]]:
    aliases: dict[str, tuple[str, str, str]] = {}

    def add(alias: str, display: str) -> None:
        key = calendar_location_key(alias)
        if key:
            aliases[key] = (key, alias, display)

    for location in known_locations:
        display = _display_location(location)
        add(location, display)
        display_key = calendar_location_key(display)
        if display_key:
            add(display, display)
        if "cambridge" in display_key:
            add("Cambridge", display)
            add("Cambridge Jockey Club", display)
        if "tearoha" in display_key:
            add("Te Aroha", display)
        if "terapa" in display_key:
            add("Te Rapa", display)

    for alias, display in {
        "Auckland Thoroughbred Racing": "Ellerslie",
        "Ellerslie": "Ellerslie",
        "Matamata RC": "Matamata",
        "Matamata": "Matamata",
        "Waikato TR": "Te Rapa",
        "Te Rapa": "Te Rapa",
        "Te Aroha JC": "Te Aroha",
        "Te Aroha": "Te Aroha",
        "Counties RC": "Pukekohe",
        "Pukekohe Park": "Pukekohe",
        "Tauranga": "Tauranga",
        "Rotorua": "Rotorua",
        "Ruakaka": "Ruakaka",
        "Cambridge": "Cambridge Synthetic",
        "Cambridge Synthetic": "Cambridge Synthetic",
        "Taupo": "Taupo",
        "Avondale": "Avondale",
    }.items():
        add(alias, display)
    return sorted((value for value in aliases.values()), key=lambda item: len(item[1]), reverse=True)


def _display_location(value: str) -> str:
    text = re.sub(r"^[THG]-", "", str(value or "").strip(), flags=re.IGNORECASE)
    text = re.sub(r"\b(?:thoroughbred|harness|greyhound)\b", "", text, flags=re.IGNORECASE).strip()
    return re.sub(r"\s+", " ", text) or "Race day"


def _match_known_location(row_text: str, aliases: list[tuple[str, str, str]]) -> tuple[str, str] | None:
    row_key = calendar_location_key(row_text)
    for alias_key, _alias, display in aliases:
        if alias_key and alias_key in row_key:
            return display, calendar_location_key(display)
    return None


def _club_name_from_row(row_text: str, racecourse: str) -> str:
    text = DATE_RE.sub("", row_text, count=1).strip(" -")
    text = re.sub(re.escape(racecourse), "", text, flags=re.IGNORECASE).strip(" -")
    text = re.sub(r"\b(P|TRIAL|Trial|Trials)\b$", "", text).strip()
    return re.sub(r"\s+", " ", text)[:120]
