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
from app.account_invitations import account_invite_details, create_account_invite
from app.contractors import create_invite, invite_details
from app.config import get_settings
from app.database import count_app_users, get_connection, init_db
from app.deputy_web import run_deputy_web_capture
from app import main as main_module
from app.main import app
from app.url_safety import PinnedHTTPSClient, fetch_public_https, normalize_deputy_web_url, validate_public_https_url
from fastapi.testclient import TestClient


def public_resolver(host: str, port: int, **_: object):
    address = host if host in {"127.0.0.1", "::1", "169.254.169.254", "192.0.2.1"} else ("127.0.0.1" if host == "private.example" else "93.184.216.34")
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
empty_settings = replace(settings, deputy_web_url="")
empty_result = asyncio.run(run_deputy_web_capture(empty_settings))
assert empty_result.status == "missing" and "incomplete" in empty_result.message.lower()

assert validate_public_https_url("https://calendar.example/feed?token=redacted", resolver=public_resolver).startswith("https://calendar.example/")
for bad in ("http://calendar.example/feed", "https://user:pass@calendar.example/feed", "https://calendar.example:444/feed",
            "https://private.example/feed", "https://127.0.0.1/feed", "https://[::1]/feed",
            "https://169.254.169.254/feed", "https://192.0.2.1/feed"):
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
    def close(self):
        pass


class FeedTransport:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []
    def get(self, url: str, **kwargs: object):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


feed = FeedTransport([FeedResponse(302, location="https://calendar.example/final"), FeedResponse(200, b"BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n")])
assert fetch_public_https("https://calendar.example/feed", transport=feed, resolver=public_resolver).startswith(b"BEGIN:VCALENDAR")
assert len(feed.calls) == 2 and all(call[1]["validated_addresses"] == ("93.184.216.34",) for call in feed.calls)
blocked_redirect = FeedTransport([FeedResponse(302, location="https://private.example/feed")])
try:
    fetch_public_https("https://calendar.example/feed", transport=blocked_redirect, resolver=public_resolver)
except ValueError:
    pass
else:
    raise AssertionError("private redirect accepted")
assert len(blocked_redirect.calls) == 1

# A changing second DNS answer cannot affect the connection because resolution occurs once and
# the transport receives only the validated address. The real transport is separately proven to
# construct its TLS pool with that address and the original hostname.
resolver_calls = []
def rebinding_resolver(host: str, port: int, **_: object):
    resolver_calls.append(host)
    address = "93.184.216.34" if len(resolver_calls) == 1 else "127.0.0.1"
    return [(2, 1, 6, "", (address, port))]
rebind_transport = FeedTransport([FeedResponse(200, b"safe")])
assert fetch_public_https("https://rebind.example/feed", transport=rebind_transport, resolver=rebinding_resolver) == b"safe"
assert resolver_calls == ["rebind.example"]
assert rebind_transport.calls[0][1]["validated_addresses"] == ("93.184.216.34",)
assert rebind_transport.calls[0][1]["original_hostname"] == "rebind.example"
assert rebind_transport.calls[0][1]["original_authority"] == "rebind.example"

class RawResponse:
    status, headers = 200, {"Content-Length": "4"}
    def stream(self, **_: object): yield b"safe"
    def release_conn(self): pass
class FakePool:
    created = []
    def __init__(self, host: str, **kwargs: object): self.host, self.kwargs, self.calls = host, kwargs, []; self.created.append(self)
    def urlopen(self, method: str, target: str, **kwargs: object): self.calls.append((method, target, kwargs)); return RawResponse()
    def close(self): pass
import app.url_safety as url_safety_module
real_pool = url_safety_module.urllib3.HTTPSConnectionPool
url_safety_module.urllib3.HTTPSConnectionPool = FakePool
try:
    pinned = PinnedHTTPSClient().get("https://rebind.example/feed?q=1", original_hostname="rebind.example",
                                    original_authority="rebind.example", validated_addresses=("93.184.216.34",), timeout=(5, 20))
    assert b"".join(pinned.iter_content(65536)) == b"safe"; pinned.close()
finally:
    url_safety_module.urllib3.HTTPSConnectionPool = real_pool
pool = FakePool.created[0]
assert pool.host == "93.184.216.34" and pool.kwargs["assert_hostname"] == "rebind.example" and pool.kwargs["server_hostname"] == "rebind.example"
assert pool.calls[0][1] == "/feed?q=1" and pool.calls[0][2]["headers"]["Host"] == "rebind.example"

too_many = FeedTransport([FeedResponse(302, location=f"https://calendar.example/{i}") for i in range(4)])
try:
    fetch_public_https("https://calendar.example/feed", transport=too_many, resolver=public_resolver)
except ValueError:
    pass
else:
    raise AssertionError("redirect limit was not enforced")
oversized = FeedTransport([FeedResponse(200, b"12345")])
try:
    fetch_public_https("https://calendar.example/feed", transport=oversized, resolver=public_resolver, max_bytes=4)
except ValueError:
    pass
else:
    raise AssertionError("calendar size limit was not enforced")

init_db()
# Shipped signup=false still permits first-user bootstrap, then closes both GET and POST.
main_module.queue_manual_sync = lambda *_args, **_kwargs: None
signup_client = TestClient(app, follow_redirects=False)
assert signup_client.get("/signup").status_code == 200
cross_site = {"sec-fetch-site": "cross-site", "origin": "https://evil.example"}
same_origin = {"origin": "http://testserver"}
cross_signup = signup_client.post("/signup", headers=cross_site, data={"deputy_email": "blocked@example.invalid", "deputy_password": "fixture-password", "pin": "1234", "pin_confirm": "1234"})
assert cross_signup.status_code == 403 and count_app_users() == 0 and not signup_client.cookies
first_signup = signup_client.post("/signup", headers=same_origin, data={"deputy_email": "first@example.invalid", "deputy_password": "fixture-password",
    "deputy_web_url": "https://example.au.deputy.com/", "pin": "1234", "pin_confirm": "1234", "next_url": "/month"})
assert first_signup.status_code == 303 and count_app_users() == 1
with get_connection() as conn:
    admin_id = int(conn.execute("SELECT id FROM app_users WHERE deputy_email='first@example.invalid'").fetchone()["id"])
    now = datetime.now(get_settings().timezone).isoformat()
    contractor_person_id = int(conn.execute("INSERT INTO crew_people(canonical_display_name, person_type, is_active, created_at, updated_at) VALUES('Origin Fixture', 'contractor', 1, ?, ?)", (now, now)).lastrowid)
contractor_invite = create_invite(contractor_person_id, admin_id)
account_invite = create_account_invite("origin.invite@example.invalid", "Origin Invite", admin_id)
assert signup_client.post(f"/contractor/invite/{contractor_invite['token']}", headers=cross_site, data={"pin": "1234", "pin_confirm": "1234"}).status_code == 403
assert invite_details(str(contractor_invite["token"]))["available"]
assert signup_client.post(f"/account/invite/{account_invite['token']}", headers=cross_site, data={"pin": "1234", "pin_confirm": "1234"}).status_code == 403
assert account_invite_details(str(account_invite["token"]))["available"]
assert signup_client.post("/login", headers={"origin": "https://evil.example"}, data={"deputy_email": "first@example.invalid", "pin": "1234"}).status_code == 403
normal_login = TestClient(app, follow_redirects=False)
assert normal_login.post("/login", headers=same_origin, data={"deputy_email": "first@example.invalid", "pin": "1234"}).status_code == 303
assert normal_login.cookies
normal_contractor = TestClient(app, follow_redirects=False)
assert normal_contractor.post(f"/contractor/invite/{contractor_invite['token']}", headers=same_origin, data={"pin": "1234", "pin_confirm": "1234"}).status_code == 303
assert not invite_details(str(contractor_invite["token"]))["available"]
normal_account = TestClient(app, follow_redirects=False)
assert normal_account.post(f"/account/invite/{account_invite['token']}", headers=same_origin, data={"display_name": "Origin Invite", "pin": "1234", "pin_confirm": "1234", "deputy_email": "", "deputy_password": ""}).status_code == 303
assert not account_invite_details(str(account_invite["token"]))["available"]
assert signup_client.get("/signup").status_code == 303
users_before_closed_signup = count_app_users()
second_signup = signup_client.post("/signup", data={"deputy_email": "second@example.invalid", "deputy_password": "fixture-password",
    "deputy_web_url": "https://example.au.deputy.com/", "pin": "1234", "pin_confirm": "1234"})
assert second_signup.status_code == 303 and count_app_users() == users_before_closed_signup and "Signup+is+currently+closed" in second_signup.headers["location"]
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
