from __future__ import annotations

import asyncio
import hashlib
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from html.parser import HTMLParser
from urllib.parse import urljoin


LOVE_RACING_CALENDAR_URL = "https://loveracing.nz/RaceInfo.aspx#bm-meeting-calendar"
MEETING_URL_RE = re.compile(r"/RaceInfo/(\d+)/Meeting-Overview\.aspx", re.IGNORECASE)
DATE_CELL_RE = re.compile(
    r"\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+(\d{1,2})\s+([A-Za-z]+)\b",
    re.IGNORECASE,
)
CLOCK_RE = re.compile(r"^\s*(\d{1,2}):(\d{2})(?:\s*([ap])\.?m\.?)?\s*$", re.IGNORECASE)
MONTHS = {
    name.lower(): index
    for index, names in enumerate(
        (
            (),
            ("Jan", "January"),
            ("Feb", "February"),
            ("Mar", "March"),
            ("Apr", "April"),
            ("May",),
            ("Jun", "June"),
            ("Jul", "July"),
            ("Aug", "August"),
            ("Sep", "Sept", "September"),
            ("Oct", "October"),
            ("Nov", "November"),
            ("Dec", "December"),
        )
    )
    for name in names
}


@dataclass(frozen=True)
class ProgrammeParseResult:
    lifecycle_status: str
    races: list[dict[str, object]]
    race_count: int | None
    first_race_time: str
    last_race_time: str
    diagnostics: tuple[str, ...]
    content_hash: str


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[dict[str, object]] = []
        self._stack: list[dict[str, object]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = {key.lower(): value or "" for key, value in attrs}
        if tag == "table":
            table = {
                "class": attrs_dict.get("class", ""),
                "rows": [],
                "_row": None,
                "_cell": None,
            }
            self._stack.append(table)
            return
        if not self._stack:
            return
        table = self._stack[-1]
        if tag == "tr" and table["_row"] is None:
            table["_row"] = {"cells": []}
        elif tag in {"td", "th"} and table["_row"] is not None:
            table["_cell"] = {"tag": tag, "text": [], "links": []}
        elif tag == "a" and table["_cell"] is not None:
            href = attrs_dict.get("href", "").strip()
            if href:
                table["_cell"]["links"].append(href)

    def handle_endtag(self, tag: str) -> None:
        if not self._stack:
            return
        table = self._stack[-1]
        if tag in {"td", "th"} and table["_cell"] is not None and table["_row"] is not None:
            cell = table["_cell"]
            cell["text"] = " ".join("".join(cell["text"]).split())
            table["_row"]["cells"].append(cell)
            table["_cell"] = None
        elif tag == "tr" and table["_row"] is not None:
            table["rows"].append(table["_row"])
            table["_row"] = None
            table["_cell"] = None
        elif tag == "table":
            table = self._stack.pop()
            table.pop("_row", None)
            table.pop("_cell", None)
            self.tables.append(table)

    def handle_data(self, data: str) -> None:
        if self._stack and self._stack[-1]["_cell"] is not None:
            self._stack[-1]["_cell"]["text"].append(data)


def _tables(html: str) -> list[dict[str, object]]:
    parser = _TableParser()
    parser.feed(html)
    return parser.tables


def normalise_scheduled_clock(value: object) -> str:
    match = CLOCK_RE.fullmatch(str(value or ""))
    if not match:
        return ""
    hour = int(match.group(1))
    minute = int(match.group(2))
    meridiem = str(match.group(3) or "").lower()
    if minute > 59 or hour > (12 if meridiem else 23):
        return ""
    if meridiem:
        if hour == 12:
            hour = 0
        if meridiem == "p":
            hour += 12
    return f"{hour:02d}:{minute:02d}"


def parse_meeting_programme(html: str) -> ProgrammeParseResult:
    tables = _tables(html)
    has_race_header = any(
        {"race", "start"}.issubset(
            {
                str(cell.get("text") or "").strip().lower()
                for row in table.get("rows", [])
                for cell in row.get("cells", [])
                if str(cell.get("tag") or "") == "th"
            }
        )
        or {"race", "scheduled start"}.issubset(
            {
                str(cell.get("text") or "").strip().lower()
                for row in table.get("rows", [])
                for cell in row.get("cells", [])
                if str(cell.get("tag") or "") == "th"
            }
        )
        for table in tables
    )
    candidates: dict[int, list[str]] = {}
    numbered_rows: set[int] = set()
    diagnostics: list[str] = []
    if has_race_header:
        for table in tables:
            if "overview-info" not in str(table.get("class") or "").split():
                continue
            for row in table.get("rows", []):
                cells = list(row.get("cells", []))
                if len(cells) < 2:
                    continue
                race_text = str(cells[0].get("text") or "").strip()
                if not race_text.isdigit() or int(race_text) <= 0:
                    continue
                race_number = int(race_text)
                numbered_rows.add(race_number)
                scheduled = normalise_scheduled_clock(cells[1].get("text"))
                candidates.setdefault(race_number, []).append(scheduled)

    races: list[dict[str, object]] = []
    conflicting: set[int] = set()
    for race_number in sorted(numbered_rows):
        nonblank = {value for value in candidates.get(race_number, []) if value}
        if len(nonblank) > 1:
            conflicting.add(race_number)
            diagnostics.append(f"Race {race_number} has conflicting scheduled starts.")
            continue
        races.append(
            {
                "number": race_number,
                "scheduled_start": next(iter(nonblank), ""),
            }
        )

    maximum = max(numbered_rows, default=0)
    contiguous = bool(maximum and numbered_rows == set(range(1, maximum + 1)))
    race_count = maximum if contiguous else None
    by_number = {int(row["number"]): str(row["scheduled_start"]) for row in races}
    first_race_time = by_number.get(1, "")
    last_race_time = by_number.get(maximum, "") if maximum else ""
    scheduled_count = sum(1 for value in by_number.values() if value)
    complete = bool(
        contiguous
        and not conflicting
        and maximum
        and scheduled_count == maximum
    )
    if complete:
        lifecycle = "complete"
    elif scheduled_count:
        lifecycle = "partial"
    else:
        lifecycle = "awaiting_schedule"
    if not has_race_header:
        diagnostics.append("Race/Start summary header was not found.")
    if numbered_rows and not contiguous:
        diagnostics.append("Race numbers were not a contiguous sequence.")
    return ProgrammeParseResult(
        lifecycle_status=lifecycle,
        races=races,
        race_count=race_count,
        first_race_time=first_race_time,
        last_race_time=last_race_time,
        diagnostics=tuple(diagnostics),
        content_hash=hashlib.sha256(html.encode("utf-8", errors="replace")).hexdigest(),
    )


def parse_calendar_meeting_identities(html: str, year: int) -> list[dict[str, object]]:
    identities: list[dict[str, object]] = []
    seen: set[str] = set()
    for table in _tables(html):
        for row in table.get("rows", []):
            cells = list(row.get("cells", []))
            if len(cells) < 3:
                continue
            meeting_url = ""
            meeting_id = ""
            for cell in cells:
                for href in cell.get("links", []):
                    match = MEETING_URL_RE.search(str(href))
                    if match:
                        meeting_id = match.group(1)
                        meeting_url = urljoin(LOVE_RACING_CALENDAR_URL, str(href))
                        break
                if meeting_id:
                    break
            if not meeting_id or meeting_id in seen:
                continue
            date_match = next(
                (
                    DATE_CELL_RE.search(str(cell.get("text") or ""))
                    for cell in cells
                    if DATE_CELL_RE.search(str(cell.get("text") or ""))
                ),
                None,
            )
            if not date_match:
                continue
            month = MONTHS.get(date_match.group(2).lower())
            if not month:
                continue
            try:
                meeting_date = date(year, month, int(date_match.group(1)))
            except ValueError:
                continue
            link_cell_index = next(
                (
                    index
                    for index, cell in enumerate(cells)
                    if any(MEETING_URL_RE.search(str(link)) for link in cell.get("links", []))
                ),
                -1,
            )
            club = str(cells[link_cell_index].get("text") or "").strip() if link_cell_index >= 0 else ""
            venue = str(cells[-1].get("text") or "").strip()
            if not venue or venue.lower() in {"racecourse", "course"}:
                continue
            seen.add(meeting_id)
            identities.append(
                {
                    "meeting_id": meeting_id,
                    "meeting_url": meeting_url,
                    "DateISO": meeting_date.isoformat(),
                    "Racecourse": venue,
                    "Club": club,
                    "MarketingName": club,
                    "WebMeetingType": "R",
                }
            )
    return identities


def programme_refresh_due(
    detail: dict[str, object],
    now: datetime,
    *,
    manual: bool = False,
) -> tuple[bool, str]:
    if manual:
        return True, "manual"
    next_retry = _parse_datetime(detail.get("next_retry_at"))
    if next_retry and now < next_retry:
        return False, "failure backoff"
    meeting_date = _parse_date(detail.get("meeting_date"))
    if meeting_date is None:
        return False, "meeting date unavailable"
    checked_at = _parse_datetime(detail.get("page_last_checked_at"))
    lifecycle = str(detail.get("lifecycle_status") or "discovered")
    first_race = normalise_scheduled_clock(detail.get("first_race_time"))

    if now.date() > meeting_date:
        return (not bool(detail.get("post_meeting_checked_at")), "post-meeting confirmation")
    if lifecycle == "complete":
        if (
            now.date() == meeting_date
            and now.time() >= time(6, 0)
            and (not first_race or now.time() < time.fromisoformat(first_race))
            and not detail.get("race_morning_confirmed_at")
        ):
            return True, "race-morning confirmation"
        return False, "complete"

    meeting_midnight = datetime.combine(meeting_date, time.min, tzinfo=now.tzinfo)
    hours_until = (meeting_midnight - now).total_seconds() / 3600
    if now.date() == meeting_date:
        interval = timedelta(hours=1)
        reason = "race-day incomplete"
    elif hours_until <= 24:
        interval = timedelta(hours=2)
        reason = "inside 24 hours"
    elif hours_until <= 72:
        interval = timedelta(hours=6)
        reason = "inside 72 hours"
    else:
        interval = timedelta(hours=24)
        reason = "daily discovery"
    return (checked_at is None or now - checked_at >= interval, reason)


def failure_backoff(attempts: int) -> timedelta:
    minutes = (15, 30, 60, 120, 240, 360)
    return timedelta(minutes=minutes[min(max(1, attempts) - 1, len(minutes) - 1)])


def _parse_datetime(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _parse_date(value: object) -> date | None:
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


async def _capture_calendar_months_async(months: list[tuple[int, int]]) -> list[dict[str, object]]:
    from playwright.async_api import async_playwright

    results: list[dict[str, object]] = []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True, args=["--no-sandbox"])
        page = await browser.new_page()
        await page.goto(LOVE_RACING_CALENDAR_URL, wait_until="domcontentloaded", timeout=45_000)
        await page.wait_for_selector("#calendar .fc-header-title h2", timeout=30_000)
        for target_year, target_month in sorted(set(months)):
            for _step in range(24):
                title = (await page.locator("#calendar .fc-header-title h2").inner_text()).strip()
                match = re.search(r"([A-Za-z]+)\s+(\d{4})", title)
                if not match:
                    raise RuntimeError("Love Racing calendar month heading was not recognised.")
                current_month = MONTHS.get(match.group(1).lower())
                current_year = int(match.group(2))
                current_index = (current_year * 12) + int(current_month or 0)
                target_index = (target_year * 12) + target_month
                if current_index == target_index:
                    break
                selector = ".fc-button-next" if current_index < target_index else ".fc-button-prev"
                old_title = title
                await page.locator(f"#calendar {selector}").click()
                await page.wait_for_function(
                    "(oldTitle) => document.querySelector('#calendar .fc-header-title h2')?.textContent.trim() !== oldTitle",
                    old_title,
                    timeout=30_000,
                )
            else:
                raise RuntimeError("Love Racing calendar month navigation exceeded its safety limit.")
            results.extend(parse_calendar_meeting_identities(await page.content(), target_year))
        await browser.close()
    deduped = {str(item["meeting_id"]): item for item in results}
    return list(deduped.values())


def capture_calendar_months(months: list[tuple[int, int]]) -> list[dict[str, object]]:
    return asyncio.run(_capture_calendar_months_async(months))


async def _capture_meeting_pages_async(meetings: list[dict[str, object]]) -> list[dict[str, object]]:
    from playwright.async_api import async_playwright

    results: list[dict[str, object]] = []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True, args=["--no-sandbox"])
        page = await browser.new_page()
        for meeting in meetings:
            meeting_id = str(meeting.get("meeting_id") or "")
            meeting_url = str(meeting.get("meeting_url") or "")
            try:
                await page.goto(meeting_url, wait_until="domcontentloaded", timeout=45_000)
                await page.wait_for_selector("body", timeout=15_000)
                results.append(
                    {
                        "meeting_id": meeting_id,
                        "html": await page.content(),
                        "error": "",
                    }
                )
            except Exception as exc:
                results.append(
                    {
                        "meeting_id": meeting_id,
                        "html": "",
                        "error": f"{type(exc).__name__}: {str(exc)[:300]}",
                    }
                )
        await browser.close()
    return results


def capture_meeting_pages(meetings: list[dict[str, object]]) -> list[dict[str, object]]:
    return asyncio.run(_capture_meeting_pages_async(meetings))
