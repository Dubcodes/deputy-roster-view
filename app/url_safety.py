from __future__ import annotations

import ipaddress
import re
import socket
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests
import urllib3

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


def _validated_public_https_target(value: str, *, resolver=socket.getaddrinfo) -> tuple[str, str, str, tuple[str, ...]]:
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
        parsed_addresses = tuple(sorted(addresses, key=lambda value: (ipaddress.ip_address(value).version, value)))
    except (OSError, ValueError) as exc:
        raise ValueError("Calendar host could not be resolved safely.") from exc
    if not parsed_addresses or any(not ipaddress.ip_address(address).is_global for address in parsed_addresses):
        raise ValueError("Calendar URL cannot use a local, private, or reserved network address.")
    normalized = urlunsplit(("https", parsed.netloc.lower(), parsed.path or "/", parsed.query, ""))
    return normalized, host, parsed.netloc.lower(), parsed_addresses


def validate_public_https_url(value: str, *, resolver=socket.getaddrinfo) -> str:
    return _validated_public_https_target(value, resolver=resolver)[0]


class _PinnedResponse:
    def __init__(self, response: urllib3.response.BaseHTTPResponse, pool: urllib3.HTTPSConnectionPool):
        self.status_code = int(response.status)
        self.ok = 200 <= self.status_code < 300
        self.headers = response.headers
        self._response = response
        self._pool = pool

    def iter_content(self, chunk_size: int):
        yield from self._response.stream(amt=chunk_size, decode_content=True)

    def close(self) -> None:
        self._response.release_conn()
        self._pool.close()


class PinnedHTTPSClient:
    """Connect only to validated IPs while authenticating the original HTTPS host."""

    def get(self, url: str, *, original_hostname: str, original_authority: str,
            validated_addresses: tuple[str, ...], timeout: tuple[int, int]) -> _PinnedResponse:
        parsed = urlsplit(url)
        request_target = urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
        last_error: Exception | None = None
        for address in validated_addresses:
            pool = urllib3.HTTPSConnectionPool(
                address,
                port=443,
                maxsize=1,
                block=True,
                cert_reqs="CERT_REQUIRED",
                ca_certs=requests.certs.where(),
                assert_hostname=original_hostname,
                server_hostname=original_hostname,
            )
            try:
                response = pool.urlopen(
                    "GET",
                    request_target,
                    headers={"Host": original_authority, "Accept-Encoding": "identity"},
                    redirect=False,
                    retries=False,
                    preload_content=False,
                    timeout=urllib3.Timeout(connect=timeout[0], read=timeout[1]),
                )
                return _PinnedResponse(response, pool)
            except urllib3.exceptions.HTTPError as exc:
                last_error = exc
                pool.close()
        raise requests.ConnectionError("Calendar host could not be reached at a validated address.") from last_error


def fetch_public_https(url: str, *, transport: object | None = None, resolver=socket.getaddrinfo,
                       timeout: tuple[int, int] = (5, 20), max_bytes: int = MAX_ICAL_BYTES) -> bytes:
    client = transport or PinnedHTTPSClient()
    current = str(url)
    for redirect_count in range(MAX_ICAL_REDIRECTS + 1):
        current, hostname, authority, addresses = _validated_public_https_target(current, resolver=resolver)
        response = client.get(current, original_hostname=hostname, original_authority=authority,
                              validated_addresses=addresses, timeout=timeout)
        try:
            if 300 <= int(response.status_code) < 400:
                location = response.headers.get("Location", "")
                if not location or redirect_count >= MAX_ICAL_REDIRECTS:
                    raise ValueError("Calendar feed exceeded the safe redirect limit.")
                current = urljoin(current, location)
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
        finally:
            response.close()
    raise ValueError("Calendar feed exceeded the safe redirect limit.")
