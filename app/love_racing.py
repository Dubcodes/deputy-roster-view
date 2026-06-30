from __future__ import annotations

import hashlib
import io
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from html import unescape

import requests
from pypdf import PdfReader

from .database import calendar_location_key


LOVE_RACING_URL = "https://loveracing.nz/RaceInfo.aspx#bm-meeting-calendar"
LOVE_RACING_FETCH_URL = "https://loveracing.nz/RaceInfo.aspx"
LOVE_RACING_CALENDAR_ENDPOINT = "https://loveracing.nz/ServerScript/RaceInfo.aspx/GetCalendarEvents"
LOVE_RACING_FETCH_URLS = (
    LOVE_RACING_CALENDAR_ENDPOINT,
    "https://www.loveracing.nz/ServerScript/RaceInfo.aspx/GetCalendarEvents",
)
NZTR_CALENDAR_PDF_URL = (
    "https://nztr.co.nz/sites/nztrindustry/files/2026-05/"
    "2026.27%20Final%20Racing%20Calendar%20-%20TAB%20NZ.pdf"
)
PDF_COLUMN_ANCHORS = (31.0, 249.3, 467.5, 685.8, 904.0, 1122.2, 1340.5)
PDF_EVENT_RE = re.compile(r"\(x\d+\)", re.IGNORECASE)
PDF_NON_THOROUGHBRED_RE = re.compile(r"\b(?:HRC|TC)\b|Harness|Metro", re.IGNORECASE)
PDF_THOROUGHBRED_RE = re.compile(r"\b(?:RC|JC|TR|TRI|HC|RI)\b|Racing", re.IGNORECASE)
PDF_FIRST_WEEK_RE = re.compile(r"Monday,\s+(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})", re.IGNORECASE)
PDF_WEEK_RE = re.compile(r"(\d{1,2})-([A-Za-z]{3,9})", re.IGNORECASE)
LOVE_RACING_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-NZ,en;q=0.9",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Referer": "https://loveracing.nz/",
}
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
    source_url: str = LOVE_RACING_FETCH_URL
    status_code: int = 0
    content_length: int = 0
    attempts: tuple[str, ...] = ()


class LoveRacingFetchError(RuntimeError):
    def __init__(self, attempts: list[str]) -> None:
        self.attempts = tuple(attempts)
        super().__init__("Love Racing request was blocked or failed. " + " | ".join(attempts))


def fetch_love_racing_meetings(known_locations: list[str], today: date | None = None) -> LoveRacingResult:
    today = today or date.today()
    used_pdf_fallback = False
    try:
        response, attempts, events = _fetch_love_racing_events(today)
    except LoveRacingFetchError as endpoint_error:
        try:
            response, fallback_attempts, events = _fetch_nztr_calendar_events()
        except LoveRacingFetchError as fallback_error:
            raise LoveRacingFetchError(list(endpoint_error.attempts) + list(fallback_error.attempts)) from fallback_error
        attempts = list(endpoint_error.attempts) + fallback_attempts
        used_pdf_fallback = True
    meetings = parse_love_racing_events(events, known_locations=known_locations, today=today)
    source_label = "official NZTR calendar" if used_pdf_fallback else "Love Racing calendar"
    return LoveRacingResult(
        meetings=meetings,
        fetched_rows=len(events),
        matched_rows=len(meetings),
        message=f"The {source_label} found {len(meetings)} known future race days.",
        source_url=response.url,
        status_code=response.status_code,
        content_length=len(response.content or b""),
        attempts=tuple(attempts),
    )


def _fetch_love_racing_events(today: date) -> tuple[requests.Response, list[str], list[dict[str, object]]]:
    attempts: list[str] = []
    start_day = today - timedelta(days=1)
    end_day = today + timedelta(days=430)
    payload = {
        "start": start_day.strftime("%d-%b-%Y"),
        "end": end_day.strftime("%d-%b-%Y"),
    }
    with requests.Session() as session:
        session.headers.update(LOVE_RACING_HEADERS)
        session.headers.update(
            {
                "Content-Type": "application/json; charset=utf-8",
                "X-Requested-With": "XMLHttpRequest",
            }
        )
        for url in LOVE_RACING_FETCH_URLS:
            try:
                response = session.post(url, json=payload, timeout=25, allow_redirects=True)
            except requests.RequestException as exc:
                attempts.append(f"{url}: {type(exc).__name__}: {exc}")
                continue
            attempts.append(f"{url}: HTTP {response.status_code} ({len(response.content or b'')} bytes)")
            if response.status_code == 200 and response.text.strip():
                try:
                    wrapper = response.json()
                    events = json.loads(wrapper.get("d") or "[]")
                except (TypeError, ValueError, json.JSONDecodeError) as exc:
                    attempts[-1] = f"{url}: JSON parse failed: {exc}"
                    continue
                if isinstance(events, list):
                    return response, attempts, [event for event in events if isinstance(event, dict)]
                attempts[-1] = f"{url}: JSON payload was not a list"
                continue
            if response.status_code not in {403, 404, 429, 500, 502, 503, 504}:
                try:
                    response.raise_for_status()
                except requests.HTTPError as exc:
                    attempts[-1] = f"{url}: {type(exc).__name__}: {exc}"
    raise LoveRacingFetchError(attempts)


def _fetch_nztr_calendar_events() -> tuple[requests.Response, list[str], list[dict[str, object]]]:
    attempts: list[str] = []
    headers = dict(LOVE_RACING_HEADERS)
    headers["Accept"] = "application/pdf,*/*;q=0.8"
    try:
        response = requests.get(NZTR_CALENDAR_PDF_URL, headers=headers, timeout=45, allow_redirects=True)
    except requests.RequestException as exc:
        raise LoveRacingFetchError([f"Official NZTR calendar: {type(exc).__name__}: {exc}"]) from exc
    attempts.append(f"Official NZTR calendar: HTTP {response.status_code} ({len(response.content or b'')} bytes)")
    if response.status_code != 200:
        raise LoveRacingFetchError(attempts)
    try:
        events = parse_nztr_calendar_pdf(response.content)
    except Exception as exc:
        attempts.append(f"Official NZTR calendar parse failed: {type(exc).__name__}: {exc}")
        raise LoveRacingFetchError(attempts) from exc
    if not events:
        attempts.append("Official NZTR calendar contained no thoroughbred meetings")
        raise LoveRacingFetchError(attempts)
    return response, attempts, events


def parse_nztr_calendar_pdf(content: bytes) -> list[dict[str, object]]:
    pages: list[list[tuple[float, float, str, float]]] = []
    reader = PdfReader(io.BytesIO(content))
    for page in reader.pages:
        fragments: list[tuple[float, float, str, float]] = []

        def collect_text(text: str, _cm: object, tm: list[float], _font: object, size: float) -> None:
            cleaned = " ".join(str(text or "").split())
            if cleaned:
                fragments.append((float(tm[4]), float(tm[5]), cleaned, float(size)))

        page.extract_text(visitor_text=collect_text)
        pages.append(fragments)
    return parse_nztr_calendar_fragments(pages)


def parse_nztr_calendar_fragments(
    pages: list[list[tuple[float, float, str, float]]],
) -> list[dict[str, object]]:
    current_year: int | None = None
    previous_month: int | None = None
    events: list[dict[str, object]] = []
    for fragments in pages:
        weeks: list[tuple[float, date]] = []
        for x, y, text, _size in fragments:
            full_match = PDF_FIRST_WEEK_RE.fullmatch(text)
            if full_match:
                month_number = MONTHS.get(full_match.group(2).lower())
                if month_number:
                    current_year = int(full_match.group(3))
                    previous_month = month_number
                    weeks.append((y, date(current_year, month_number, int(full_match.group(1)))))
                continue
            short_match = PDF_WEEK_RE.fullmatch(text)
            if not short_match or x <= 1 or current_year is None:
                continue
            month_number = MONTHS.get(short_match.group(2).lower())
            if not month_number:
                continue
            if previous_month is not None and month_number < previous_month - 6:
                current_year += 1
            previous_month = month_number
            weeks.append((y, date(current_year, month_number, int(short_match.group(1)))))
        weeks.sort(key=lambda item: item[0])

        for x, y, text, _size in fragments:
            if not PDF_EVENT_RE.search(text) or not _is_thoroughbred_pdf_event(text):
                continue
            prior_weeks = [week for week in weeks if week[0] < y]
            if not prior_weeks:
                continue
            _week_y, monday = max(prior_weeks, key=lambda item: item[0])
            day_index = min(range(7), key=lambda index: abs(x - PDF_COLUMN_ANCHORS[index]))
            meeting_date = monday + timedelta(days=day_index)
            club_name = re.sub(r"\(x\d+\).*", "", text, flags=re.IGNORECASE).split("@", 1)[0].strip(" ,")
            events.append(
                {
                    "DateISO": meeting_date.isoformat(),
                    "Racecourse": text,
                    "Club": club_name,
                    "MarketingName": text,
                    "WebMeetingType": "R",
                }
            )
    return events


def _is_thoroughbred_pdf_event(text: str) -> bool:
    return not PDF_NON_THOROUGHBRED_RE.search(text) and bool(PDF_THOROUGHBRED_RE.search(text))


def parse_love_racing_events(
    events: list[dict[str, object]],
    known_locations: list[str],
    today: date | None = None,
) -> list[dict[str, object]]:
    today = today or date.today()
    aliases = _location_aliases(known_locations)
    meetings: list[dict[str, object]] = []
    seen: set[tuple[str, str, str]] = set()
    for event in events:
        meeting_type = str(event.get("WebMeetingType") or "").strip().upper()
        if meeting_type and meeting_type != "R":
            continue
        meeting_date = _meeting_date_from_event(event)
        if meeting_date is None or meeting_date < today:
            continue
        row_text = _event_text(event)
        match = _match_known_location(row_text, aliases)
        if match is None:
            continue
        racecourse, racecourse_key = match
        club_name = str(event.get("Club") or event.get("MarketingName") or "").strip()
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


def _event_text(event: dict[str, object]) -> str:
    return " ".join(
        str(event.get(key) or "").strip()
        for key in ("Racecourse", "TrackAAPName", "Club", "MarketingName", "WebMeetingType")
        if str(event.get(key) or "").strip()
    )


def _meeting_date_from_event(event: dict[str, object]) -> date | None:
    date_iso = str(event.get("DateISO") or "").strip()
    if date_iso:
        try:
            return date.fromisoformat(date_iso[:10])
        except ValueError:
            pass
    raw = str(event.get("RaceDate") or "")
    match = re.search(r"/Date\((\d+)", raw)
    if match:
        try:
            return (datetime.utcfromtimestamp(int(match.group(1)) / 1000) + timedelta(hours=12)).date()
        except (OSError, OverflowError, ValueError):
            return None
    day = str(event.get("Day") or "").strip()
    month = str(event.get("Month") or event.get("MonthName") or "").strip().lower()
    year = str(event.get("Year") or "").strip()
    if day and month and year:
        month_number = MONTHS.get(month)
        if month_number:
            try:
                return date(int(year), month_number, int(day))
            except ValueError:
                return None
    return None


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
        row_text = _trim_section_tail(row_text)
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
    known_display_keys = {
        calendar_location_key(_display_location(location))
        for location in known_locations
        if calendar_location_key(_display_location(location))
    }

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
        "Ellerslie": "Ellerslie",
        "Matamata RC": "Matamata",
        "Matamata": "Matamata",
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
        "CAMB SYNTH": "Cambridge Synthetic",
        "Taupo": "Taupo",
        "Avondale": "Avondale",
        "Arawa Park": "Rotorua",
        "Whangarei RC": "Ruakaka",
    }.items():
        display_key = calendar_location_key(display)
        if any(display_key in known_key or known_key in display_key for known_key in known_display_keys):
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
    text = _trim_section_tail(DATE_RE.sub("", row_text, count=1).strip(" -"))
    text = re.sub(re.escape(racecourse), "", text, flags=re.IGNORECASE).strip(" -")
    text = re.sub(r"\b(P|TRIAL|Trial|Trials)\b$", "", text).strip()
    return re.sub(r"\s+", " ", text)[:120]


def _trim_section_tail(row_text: str) -> str:
    parts = re.split(
        r"\b(?:Fields|Nominations|Results|Race Meeting Calendar|Trials)\s+Date\b",
        row_text,
        maxsplit=1,
        flags=re.IGNORECASE,
    )
    return parts[0].strip()
