from __future__ import annotations

import ipaddress
import re
import socket
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests

DEPUTY_INSTALL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.(?:au|eu|uk|us)\.deputy\.com$", re.I)
MAX_ICAL_BYTES = 5 * 1024 * 1024
MAX_ICAL_REDIRECTS = 3


def normalize_deputy_web_url(value: str) -> str:
    parsed = urlsplit(str(value or "").strip())
    host = (parsed.hostname or "").lower()
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Deputy URL has an invalid port.") from exc
    if (parsed.scheme.lower() != "https" or not DEPUTY_INSTALL_RE.fullmatch(host)
            or parsed.username or parsed.password or port not in (None, 443)
            or parsed.path not in ("", "/") or parsed.query or parsed.fragment not in ("", "/")):
        raise ValueError("Deputy URL must be an HTTPS Deputy install URL such as https://example.au.deputy.com/.")
    return f"https://{host}/#/"


def validate_public_https_url(value: str, *, resolver=socket.getaddrinfo) -> str:
    parsed = urlsplit(str(value or "").strip())
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Calendar URL has an invalid port.") from exc
    if (parsed.scheme.lower() != "https" or not parsed.hostname or parsed.username or parsed.password or port not in (None, 443)):
        raise ValueError("Calendar URL must be HTTPS without credentials or a custom port.")
    host = parsed.hostname.lower()
    try:
        addresses = {item[4][0].split("%", 1)[0] for item in resolver(host, 443, type=socket.SOCK_STREAM)}
    except OSError as exc:
        raise ValueError("Calendar host could not be resolved safely.") from exc
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise ValueError("Calendar URL cannot use a local, private, or reserved network address.")
    return urlunsplit(("https", parsed.netloc.lower(), parsed.path or "/", parsed.query, ""))


def fetch_public_https(url: str, *, session=requests, resolver=socket.getaddrinfo,
                       timeout: tuple[int, int] = (5, 20), max_bytes: int = MAX_ICAL_BYTES) -> bytes:
    current = validate_public_https_url(url, resolver=resolver)
    for redirect_count in range(MAX_ICAL_REDIRECTS + 1):
        response = session.get(current, timeout=timeout, allow_redirects=False, stream=True)
        if 300 <= int(response.status_code) < 400:
            location = response.headers.get("Location", "")
            if not location or redirect_count >= MAX_ICAL_REDIRECTS:
                raise ValueError("Calendar feed exceeded the safe redirect limit.")
            current = validate_public_https_url(urljoin(current, location), resolver=resolver)
            continue
        if not response.ok:
            raise requests.HTTPError(f"HTTP {response.status_code}")
        content_length = response.headers.get("Content-Length")
        if content_length and int(content_length) > max_bytes:
            raise ValueError("Calendar feed is larger than the safe download limit.")
        chunks, total = [], 0
        for chunk in response.iter_content(chunk_size=65536):
            total += len(chunk)
            if total > max_bytes:
                raise ValueError("Calendar feed is larger than the safe download limit.")
            chunks.append(chunk)
        return b"".join(chunks)
    raise ValueError("Calendar feed exceeded the safe redirect limit.")
