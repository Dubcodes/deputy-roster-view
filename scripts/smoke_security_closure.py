from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

tmp = Path(tempfile.mkdtemp(prefix="redeputy-security-"))
os.environ.update({"DB_PATH": str(tmp / "security.sqlite3"), "DATA_DIR": str(tmp), "APP_SECRET_KEY": "obvious-test-key"})
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.auth import clear_login_failures, login_is_throttled, record_login_failure
from app.config import get_settings
from app.database import get_connection, init_db
from app.deputy_web import run_deputy_web_capture
from app.url_safety import fetch_public_https, normalize_deputy_web_url, validate_public_https_url


def public_resolver(host: str, port: int, **_: object):
    address = "93.184.216.34" if host != "private.example" else "127.0.0.1"
    return [(2, 1, 6, "", (address, port))]


assert normalize_deputy_web_url("https://gate.au.deputy.com/#/") == "https://gate.au.deputy.com/#/"
for bad in ("http://gate.au.deputy.com", "https://evil.example", "https://gate.au.deputy.com.evil.test",
            "https://evil.gate.au.deputy.com", "https://127.0.0.1", "https://192.168.0.1", "https://169.254.169.254",
            "https://gate.au.deputy.com@attacker.example", "https://user:pass@gate.au.deputy.com",
            "https://gate.au.deputy.com:444/", "https://gate.au.deputy.com/nested"):
    try:
        normalize_deputy_web_url(bad)
    except ValueError:
        pass
    else:
        raise AssertionError(f"unsafe Deputy URL accepted: {bad}")

settings = replace(get_settings(), deputy_web_url="https://evil.example", deputy_login_email="fixture@example.invalid", deputy_login_password="fixture")
result = asyncio.run(run_deputy_web_capture(settings))
assert result.status == "error" and "valid Deputy install URL" in result.message

assert validate_public_https_url("https://calendar.example/feed?token=redacted", resolver=public_resolver).startswith("https://calendar.example/")
for bad in ("http://calendar.example/feed", "https://user:pass@calendar.example/feed", "https://private.example/feed"):
    try:
        validate_public_https_url(bad, resolver=public_resolver)
    except ValueError:
        pass
    else:
        raise AssertionError(f"unsafe calendar URL accepted: {bad}")


class FeedResponse:
    def __init__(self, status: int, body: bytes = b"", location: str = ""):
        self.status_code, self.ok = status, status == 200
        self.headers = {"Location": location} if location else {"Content-Length": str(len(body))}
        self.body = body
    def iter_content(self, chunk_size: int):
        yield self.body


class FeedSession:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []
    def get(self, url: str, **kwargs: object):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


feed = FeedSession([FeedResponse(302, location="https://calendar.example/final"), FeedResponse(200, b"BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n")])
assert fetch_public_https("https://calendar.example/feed", session=feed, resolver=public_resolver).startswith(b"BEGIN:VCALENDAR")
assert len(feed.calls) == 2 and all(call[1]["allow_redirects"] is False for call in feed.calls)
blocked_redirect = FeedSession([FeedResponse(302, location="https://private.example/feed")])
try:
    fetch_public_https("https://calendar.example/feed", session=blocked_redirect, resolver=public_resolver)
except ValueError:
    pass
else:
    raise AssertionError("private redirect accepted")
assert len(blocked_redirect.calls) == 1

init_db()
moment = datetime.now(get_settings().timezone).replace(microsecond=0)
email = "fixture@example.invalid"
for index in range(5):
    assert login_is_throttled(email, now=moment) is False
    record_login_failure(email, now=moment + timedelta(seconds=index))
assert login_is_throttled(email, now=moment + timedelta(seconds=5)) is True
assert login_is_throttled(email, now=moment + timedelta(minutes=16)) is False
with get_connection() as conn:
    row = conn.execute("SELECT account_key FROM login_throttle").fetchone()
assert row is not None and email not in row["account_key"]
clear_login_failures(email)
assert login_is_throttled(email, now=moment) is False
print("security closure smoke ok")
