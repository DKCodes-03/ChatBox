from __future__ import annotations

import hashlib
import unicodedata
from collections.abc import Collection
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

DEFAULT_CONTENT_TYPES = frozenset(
    {
        "text/html",
        "application/pdf",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }
)
DEFAULT_MAX_BYTES = 10 * 1024 * 1024
DEFAULT_TIMEOUT_SECONDS = 20.0
DEFAULT_MAX_REDIRECTS = 5


class SourceFetchError(Exception):
    """Raised when a source cannot be fetched safely."""


class UnsafeSourceURLError(SourceFetchError):
    """Raised when a URL violates the HTTPS or host allowlist policy."""


class UnsupportedContentTypeError(SourceFetchError):
    """Raised when a response is not a supported document type."""


class SourceTooLargeError(SourceFetchError):
    """Raised when a response exceeds the configured byte limit."""


@dataclass(frozen=True)
class FetchedSource:
    canonical_url: str
    content_type: str
    content: bytes
    content_hash: str


def _normalize_content(content: bytes, content_type: str, encoding: str | None) -> bytes:
    if content_type != "text/html":
        return content

    try:
        text = content.decode(encoding or "utf-8")
    except (LookupError, UnicodeDecodeError) as error:
        raise SourceFetchError("HTML source has an invalid character encoding") from error

    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return unicodedata.normalize("NFC", text).encode("utf-8")


def canonicalize_url(url: str, *, allowed_hosts: Collection[str]) -> str:
    """Normalize an HTTPS source URL and reject hosts outside the allowlist."""
    parsed = urlsplit(url.strip())
    host = parsed.hostname
    allowed = {allowed_host.lower().rstrip(".") for allowed_host in allowed_hosts}

    if (
        parsed.scheme.lower() != "https"
        or host is None
        or host.lower().rstrip(".") not in allowed
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise UnsafeSourceURLError(f"URL is not an allowed HTTPS source: {url}")

    try:
        port = parsed.port
    except ValueError as error:
        raise UnsafeSourceURLError(f"URL has an invalid port: {url}") from error
    if port not in (None, 443):
        raise UnsafeSourceURLError(f"URL uses a disallowed port: {url}")

    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/")
    return urlunsplit(("https", host.lower().rstrip("."), path, parsed.query, ""))


class SourceFetcher:
    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        allowed_hosts: Collection[str],
        max_bytes: int = DEFAULT_MAX_BYTES,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_redirects: int = DEFAULT_MAX_REDIRECTS,
        allowed_content_types: Collection[str] = DEFAULT_CONTENT_TYPES,
    ) -> None:
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if max_redirects < 0:
            raise ValueError("max_redirects cannot be negative")

        self.client = client
        self.allowed_hosts = frozenset(allowed_hosts)
        self.max_bytes = max_bytes
        self.timeout_seconds = timeout_seconds
        self.max_redirects = max_redirects
        self.allowed_content_types = frozenset(
            content_type.lower() for content_type in allowed_content_types
        )

    async def fetch(self, url: str) -> FetchedSource:
        request_url = canonicalize_url(url, allowed_hosts=self.allowed_hosts)

        for redirect_count in range(self.max_redirects + 1):
            try:
                async with self.client.stream(
                    "GET",
                    request_url,
                    follow_redirects=False,
                    timeout=self.timeout_seconds,
                ) as response:
                    if response.is_redirect:
                        location = response.headers.get("location")
                        if location is None:
                            raise SourceFetchError("Redirect response has no Location header")
                        if redirect_count == self.max_redirects:
                            raise SourceFetchError("Source exceeded the redirect limit")
                        request_url = canonicalize_url(
                            urljoin(request_url, location),
                            allowed_hosts=self.allowed_hosts,
                        )
                        continue

                    if response.is_error:
                        raise SourceFetchError(
                            f"Source returned HTTP {response.status_code}: {request_url}"
                        )

                    content_type = response.headers.get("content-type", "").split(";", 1)[0]
                    content_type = content_type.strip().lower()
                    if content_type not in self.allowed_content_types:
                        raise UnsupportedContentTypeError(
                            f"Unsupported source content type: {content_type or 'missing'}"
                        )

                    content_length = response.headers.get("content-length")
                    if content_length is not None:
                        try:
                            declared_length = int(content_length)
                        except ValueError:
                            declared_length = 0
                        if declared_length > self.max_bytes:
                            raise SourceTooLargeError(
                                f"Source exceeds the {self.max_bytes}-byte limit"
                            )

                    chunks: list[bytes] = []
                    total_bytes = 0
                    async for chunk in response.aiter_bytes():
                        total_bytes += len(chunk)
                        if total_bytes > self.max_bytes:
                            raise SourceTooLargeError(
                                f"Source exceeds the {self.max_bytes}-byte limit"
                            )
                        chunks.append(chunk)

                    content = _normalize_content(b"".join(chunks), content_type, response.encoding)
                    return FetchedSource(
                        canonical_url=request_url,
                        content_type=content_type,
                        content=content,
                        content_hash=hashlib.sha256(content).hexdigest(),
                    )
            except httpx.HTTPError as error:
                raise SourceFetchError(f"Failed to fetch source: {request_url}") from error

        raise SourceFetchError("Source exceeded the redirect limit")
