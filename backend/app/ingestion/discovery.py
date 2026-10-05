"""Bounded, SSRF-resistant discovery of public PNW HTML and PDF sources."""

from __future__ import annotations

import ipaddress
import posixpath
import socket
from collections import deque
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from types import MappingProxyType
from typing import cast
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx
from bs4 import BeautifulSoup, Tag
from bs4.filter import SoupStrainer

from app.config import APPROVED_SOURCE_HOSTS, MAX_DOCUMENT_BYTES, Settings
from app.models.enums import MediaType

MAX_SOURCE_URL_CHARACTERS = 2_048
MAX_SOURCE_TITLE_CHARACTERS = 500
MAX_TOPIC_CHARACTERS = 128
MAX_REDIRECTS = 5
READ_CHUNK_BYTES = 64 * 1024
SUPPORTED_CONTENT_TYPES: Mapping[str, MediaType] = MappingProxyType(
    {
        "text/html": MediaType.HTML,
        "application/xhtml+xml": MediaType.HTML,
        "application/pdf": MediaType.PDF,
    }
)
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_CONTROL_CHARACTERS = frozenset(chr(value) for value in (*range(32), 127))

AddressResolver = Callable[[str, int], Iterable[str]]


class DiscoveryFailureReason(StrEnum):
    INVALID_URL = "invalid_url"
    DISALLOWED_SCHEME = "disallowed_scheme"
    DISALLOWED_HOST = "disallowed_host"
    DISALLOWED_PORT = "disallowed_port"
    DNS_UNAVAILABLE = "dns_unavailable"
    NON_PUBLIC_ADDRESS = "non_public_address"
    REDIRECT_LIMIT = "redirect_limit"
    REDIRECT_LOOP = "redirect_loop"
    REDIRECT_MISSING_LOCATION = "redirect_missing_location"
    HTTP_STATUS = "http_status"
    UNSUPPORTED_MEDIA_TYPE = "unsupported_media_type"
    DOCUMENT_TOO_LARGE = "document_too_large"
    INVALID_CANONICAL = "invalid_canonical"
    FETCH_UNAVAILABLE = "fetch_unavailable"


class DiscoveryInputError(ValueError):
    """Operator-provided discovery input is invalid."""


class _DiscoveryRejected(RuntimeError):
    def __init__(self, reason: DiscoveryFailureReason, url: str) -> None:
        super().__init__(reason.value)
        self.reason = reason
        self.url = url


@dataclass(frozen=True, slots=True)
class DiscoveryPolicy:
    """Hard upper bounds and initial host boundary for one discovery run."""

    allowed_schemes: tuple[str, ...] = ("https",)
    allowed_hosts: tuple[str, ...] = ("pnw.edu", "www.pnw.edu", "catalog.pnw.edu")
    max_crawl_depth: int = 3
    max_urls_per_run: int = 100
    max_document_bytes: int = MAX_DOCUMENT_BYTES
    max_redirects: int = MAX_REDIRECTS

    def __post_init__(self) -> None:
        schemes = tuple(item.strip().lower() for item in self.allowed_schemes)
        hosts = tuple(item.strip().lower().rstrip(".") for item in self.allowed_hosts)
        if schemes != ("https",):
            raise DiscoveryInputError("only HTTPS source discovery is supported")
        if not hosts or len(hosts) != len(set(hosts)) or set(hosts) - APPROVED_SOURCE_HOSTS:
            raise DiscoveryInputError("source hosts exceed the approved PNW boundary")
        if not 0 <= self.max_crawl_depth <= 3:
            raise DiscoveryInputError("crawl depth must be between zero and three")
        if not 1 <= self.max_urls_per_run <= 100:
            raise DiscoveryInputError("URL limit must be between one and one hundred")
        if not 1 <= self.max_document_bytes <= MAX_DOCUMENT_BYTES:
            raise DiscoveryInputError("document limit must be between one byte and twenty MiB")
        if not 0 <= self.max_redirects <= MAX_REDIRECTS:
            raise DiscoveryInputError("redirect limit must be between zero and five")
        object.__setattr__(self, "allowed_schemes", schemes)
        object.__setattr__(self, "allowed_hosts", hosts)

    @classmethod
    def from_settings(cls, settings: Settings) -> DiscoveryPolicy:
        return cls(
            allowed_schemes=settings.source_allowed_schemes,
            allowed_hosts=settings.source_allowed_hosts,
            max_crawl_depth=settings.source_max_crawl_depth,
            max_urls_per_run=settings.source_max_urls_per_run,
            max_document_bytes=settings.source_max_document_bytes,
        )


@dataclass(frozen=True, slots=True)
class SourceSeed:
    """An operator-provided source and its explicit child-host boundary."""

    url: str
    title: str
    topics: tuple[str, ...]
    allowed_child_hosts: tuple[str, ...]

    def __post_init__(self) -> None:
        if not all(isinstance(host, str) for host in self.allowed_child_hosts):
            raise DiscoveryInputError("allowed child hosts must be strings")
        title = _clean_text(self.title, maximum=MAX_SOURCE_TITLE_CHARACTERS)
        topics = tuple(
            dict.fromkeys(_clean_text(topic, maximum=MAX_TOPIC_CHARACTERS) for topic in self.topics)
        )
        child_hosts = tuple(dict.fromkeys(_clean_host(host) for host in self.allowed_child_hosts))
        if not isinstance(self.url, str) or not self.url.strip():
            raise DiscoveryInputError("source URL is required")
        if not topics:
            raise DiscoveryInputError("at least one source topic is required")
        if not child_hosts:
            raise DiscoveryInputError("at least one allowed child host is required")
        object.__setattr__(self, "url", self.url.strip())
        object.__setattr__(self, "title", title)
        object.__setattr__(self, "topics", topics)
        object.__setattr__(self, "allowed_child_hosts", child_hosts)


@dataclass(frozen=True, slots=True)
class DiscoveredDocument:
    requested_url: str
    canonical_url: str
    title: str
    topics: tuple[str, ...]
    media_type: MediaType
    content: bytes
    depth: int
    parent_url: str | None
    redirect_chain: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DiscoveryIssue:
    url: str
    depth: int
    reason: DiscoveryFailureReason


@dataclass(frozen=True, slots=True)
class DiscoveryReport:
    documents: tuple[DiscoveredDocument, ...]
    issues: tuple[DiscoveryIssue, ...]
    attempted_urls: tuple[str, ...]
    truncated: bool


@dataclass(frozen=True, slots=True)
class _QueuedUrl:
    url: str
    depth: int
    parent_url: str | None
    title: str
    topics: tuple[str, ...]
    permitted_hosts: frozenset[str]


@dataclass(frozen=True, slots=True)
class _FetchedDocument:
    requested_url: str
    final_url: str
    canonical_url: str
    media_type: MediaType
    content: bytes
    redirect_chain: tuple[str, ...]


class _RequestBudget:
    def __init__(self, maximum: int) -> None:
        self.maximum = maximum
        self.attempted: list[str] = []

    @property
    def exhausted(self) -> bool:
        return len(self.attempted) >= self.maximum

    def consume(self, url: str) -> None:
        if self.exhausted:
            raise _DiscoveryRejected(DiscoveryFailureReason.FETCH_UNAVAILABLE, url)
        self.attempted.append(url)


class SourceDiscovery:
    """Discover public documents without escaping the operator-approved source boundary."""

    def __init__(
        self,
        policy: DiscoveryPolicy,
        *,
        client: httpx.Client | None = None,
        resolver: AddressResolver | None = None,
    ) -> None:
        if not isinstance(policy, DiscoveryPolicy):
            raise TypeError("policy must be a discovery policy")
        self._policy = policy
        self._resolver = resolver or _system_resolver
        self._owns_client = client is None
        self._client = client or httpx.Client(
            follow_redirects=False,
            timeout=httpx.Timeout(15.0, connect=5.0),
            trust_env=False,
            headers={
                "Accept": "text/html,application/xhtml+xml,application/pdf",
                "User-Agent": "PNW-Student-Information-Indexer/1.0",
            },
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> SourceDiscovery:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def discover(self, seeds: Sequence[SourceSeed]) -> DiscoveryReport:
        if not isinstance(seeds, Sequence) or isinstance(seeds, (str, bytes)) or not seeds:
            raise DiscoveryInputError("at least one source seed is required")

        queue: deque[_QueuedUrl] = deque()
        issues: list[DiscoveryIssue] = []
        documents: dict[str, DiscoveredDocument] = {}
        processed_scopes: set[tuple[str, frozenset[str]]] = set()
        budget = _RequestBudget(self._policy.max_urls_per_run)
        global_hosts = frozenset(self._policy.allowed_hosts)

        for seed in seeds:
            if not isinstance(seed, SourceSeed):
                raise DiscoveryInputError("source seeds must be typed source seed values")
            child_hosts = frozenset(seed.allowed_child_hosts)
            if child_hosts - global_hosts:
                raise DiscoveryInputError("child hosts exceed the approved PNW boundary")
            seed_url = canonicalize_source_url(
                seed.url,
                allowed_hosts=global_hosts,
                allowed_schemes=self._policy.allowed_schemes,
            )
            seed_host = _url_host(seed_url)
            permitted_hosts = child_hosts | {seed_host}
            queue.append(
                _QueuedUrl(
                    url=seed_url,
                    depth=0,
                    parent_url=None,
                    title=seed.title,
                    topics=seed.topics,
                    permitted_hosts=frozenset(permitted_hosts),
                )
            )

        truncated = False
        while queue:
            current = queue.popleft()
            scope_key = (current.url, current.permitted_hosts)
            if scope_key in processed_scopes:
                continue
            if budget.exhausted:
                truncated = True
                break
            processed_scopes.add(scope_key)

            try:
                fetched = self._fetch(current.url, current.permitted_hosts, budget)
            except _DiscoveryRejected as rejected:
                issues.append(
                    DiscoveryIssue(
                        url=rejected.url,
                        depth=current.depth,
                        reason=rejected.reason,
                    )
                )
                if budget.exhausted:
                    truncated = True
                continue

            discovered = DiscoveredDocument(
                requested_url=fetched.requested_url,
                canonical_url=fetched.canonical_url,
                title=current.title,
                topics=current.topics,
                media_type=fetched.media_type,
                content=fetched.content,
                depth=current.depth,
                parent_url=current.parent_url,
                redirect_chain=fetched.redirect_chain,
            )
            existing = documents.get(discovered.canonical_url)
            if existing is None:
                documents[discovered.canonical_url] = discovered
            else:
                documents[discovered.canonical_url] = replace(
                    existing,
                    topics=tuple(dict.fromkeys((*existing.topics, *discovered.topics))),
                )

            if (
                fetched.media_type is not MediaType.HTML
                or current.depth >= self._policy.max_crawl_depth
            ):
                continue
            for child_url in _html_links(fetched.content, base_url=fetched.final_url):
                if budget.exhausted:
                    truncated = True
                    break
                try:
                    canonical_child = canonicalize_source_url(
                        child_url,
                        allowed_hosts=current.permitted_hosts,
                        allowed_schemes=self._policy.allowed_schemes,
                    )
                except _DiscoveryRejected:
                    continue
                queue.append(
                    _QueuedUrl(
                        url=canonical_child,
                        depth=current.depth + 1,
                        parent_url=fetched.canonical_url,
                        title=current.title,
                        topics=current.topics,
                        permitted_hosts=current.permitted_hosts,
                    )
                )

        return DiscoveryReport(
            documents=tuple(documents.values()),
            issues=tuple(issues),
            attempted_urls=tuple(budget.attempted),
            truncated=truncated,
        )

    def _fetch(
        self,
        requested_url: str,
        permitted_hosts: frozenset[str],
        budget: _RequestBudget,
    ) -> _FetchedDocument:
        current_url = requested_url
        redirect_chain: list[str] = []
        seen_redirects = {current_url}

        for redirect_number in range(self._policy.max_redirects + 1):
            self._verify_public_destination(current_url)
            budget.consume(current_url)
            try:
                with self._client.stream("GET", current_url) as response:
                    if response.status_code in REDIRECT_STATUSES:
                        if redirect_number >= self._policy.max_redirects:
                            raise _DiscoveryRejected(
                                DiscoveryFailureReason.REDIRECT_LIMIT,
                                current_url,
                            )
                        location = response.headers.get("location")
                        if location is None or not location.strip():
                            raise _DiscoveryRejected(
                                DiscoveryFailureReason.REDIRECT_MISSING_LOCATION,
                                current_url,
                            )
                        redirected_url = canonicalize_source_url(
                            location,
                            base_url=current_url,
                            allowed_hosts=permitted_hosts,
                            allowed_schemes=self._policy.allowed_schemes,
                        )
                        if redirected_url in seen_redirects:
                            raise _DiscoveryRejected(
                                DiscoveryFailureReason.REDIRECT_LOOP,
                                redirected_url,
                            )
                        redirect_chain.append(redirected_url)
                        seen_redirects.add(redirected_url)
                        current_url = redirected_url
                        continue
                    if response.status_code != 200:
                        raise _DiscoveryRejected(DiscoveryFailureReason.HTTP_STATUS, current_url)

                    media_type = _media_type(response.headers.get("content-type"), current_url)
                    declared_length = _content_length(response.headers.get("content-length"))
                    if (
                        declared_length is not None
                        and declared_length > self._policy.max_document_bytes
                    ):
                        raise _DiscoveryRejected(
                            DiscoveryFailureReason.DOCUMENT_TOO_LARGE,
                            current_url,
                        )
                    content = self._read_bounded(response, current_url)
            except _DiscoveryRejected:
                raise
            except (httpx.HTTPError, OSError):
                raise _DiscoveryRejected(
                    DiscoveryFailureReason.FETCH_UNAVAILABLE,
                    current_url,
                ) from None

            canonical_url = current_url
            if media_type is MediaType.HTML:
                declared_canonical = _html_canonical(content, base_url=current_url)
                if declared_canonical is not None:
                    try:
                        canonical_url = canonicalize_source_url(
                            declared_canonical,
                            allowed_hosts=permitted_hosts,
                            allowed_schemes=self._policy.allowed_schemes,
                        )
                        self._verify_public_destination(canonical_url)
                    except _DiscoveryRejected:
                        raise _DiscoveryRejected(
                            DiscoveryFailureReason.INVALID_CANONICAL,
                            current_url,
                        ) from None
            return _FetchedDocument(
                requested_url=requested_url,
                final_url=current_url,
                canonical_url=canonical_url,
                media_type=media_type,
                content=content,
                redirect_chain=tuple(redirect_chain),
            )

        raise _DiscoveryRejected(DiscoveryFailureReason.REDIRECT_LIMIT, current_url)

    def _read_bounded(self, response: httpx.Response, url: str) -> bytes:
        body = bytearray()
        for chunk in response.iter_bytes(chunk_size=READ_CHUNK_BYTES):
            body.extend(chunk)
            if len(body) > self._policy.max_document_bytes:
                raise _DiscoveryRejected(DiscoveryFailureReason.DOCUMENT_TOO_LARGE, url)
        return bytes(body)

    def _verify_public_destination(self, url: str) -> None:
        host = _url_host(url)
        try:
            addresses = tuple(self._resolver(host, 443))
        except (OSError, socket.gaierror):
            raise _DiscoveryRejected(DiscoveryFailureReason.DNS_UNAVAILABLE, url) from None
        if not addresses:
            raise _DiscoveryRejected(DiscoveryFailureReason.DNS_UNAVAILABLE, url)
        try:
            parsed_addresses = tuple(ipaddress.ip_address(address) for address in addresses)
        except ValueError:
            raise _DiscoveryRejected(DiscoveryFailureReason.DNS_UNAVAILABLE, url) from None
        if any(not address.is_global for address in parsed_addresses):
            raise _DiscoveryRejected(DiscoveryFailureReason.NON_PUBLIC_ADDRESS, url)


def canonicalize_source_url(
    value: str,
    *,
    allowed_hosts: Iterable[str],
    allowed_schemes: Iterable[str] = ("https",),
    base_url: str | None = None,
) -> str:
    """Return a fragment-free canonical URL within an explicit PNW host boundary."""

    if not isinstance(value, str):
        raise _DiscoveryRejected(DiscoveryFailureReason.INVALID_URL, "")
    candidate = value.strip()
    if (
        not candidate
        or len(candidate) > MAX_SOURCE_URL_CHARACTERS
        or "\\" in candidate
        or any(character.isspace() or character in _CONTROL_CHARACTERS for character in candidate)
    ):
        raise _DiscoveryRejected(DiscoveryFailureReason.INVALID_URL, candidate)
    absolute = urljoin(base_url, candidate) if base_url is not None else candidate
    try:
        parsed = urlsplit(absolute)
        port = parsed.port
        host = _clean_host(parsed.hostname)
    except (TypeError, ValueError):
        raise _DiscoveryRejected(DiscoveryFailureReason.INVALID_URL, candidate) from None

    schemes = frozenset(item.strip().lower() for item in allowed_schemes)
    hosts = frozenset(_clean_host(item) for item in allowed_hosts)
    scheme = parsed.scheme.lower()
    if scheme not in schemes:
        raise _DiscoveryRejected(DiscoveryFailureReason.DISALLOWED_SCHEME, absolute)
    if host not in hosts:
        raise _DiscoveryRejected(DiscoveryFailureReason.DISALLOWED_HOST, absolute)
    if port not in (None, 443):
        raise _DiscoveryRejected(DiscoveryFailureReason.DISALLOWED_PORT, absolute)
    if parsed.username is not None or parsed.password is not None:
        raise _DiscoveryRejected(DiscoveryFailureReason.INVALID_URL, absolute)

    path = _normalize_path(parsed.path)
    return urlunsplit((scheme, host, path, parsed.query, ""))


def _normalize_path(path: str) -> str:
    if not path:
        return "/"
    trailing_slash = path.endswith("/")
    normalized = posixpath.normpath(path)
    if not normalized.startswith("/"):
        normalized = f"/{normalized}"
    if trailing_slash and normalized != "/":
        normalized = f"{normalized}/"
    return normalized


def _clean_text(value: object, *, maximum: int) -> str:
    if not isinstance(value, str):
        raise DiscoveryInputError("source text value is invalid")
    cleaned = " ".join(value.split())
    if not cleaned or len(cleaned) > maximum or any(item in _CONTROL_CHARACTERS for item in value):
        raise DiscoveryInputError("source text value is invalid")
    return cleaned


def _clean_host(value: object) -> str:
    if not isinstance(value, str):
        raise DiscoveryInputError("source host is invalid")
    cleaned = value.strip().lower().rstrip(".")
    if not cleaned or len(cleaned) > 253 or any(item.isspace() for item in cleaned):
        raise DiscoveryInputError("source host is invalid")
    return cleaned


def _url_host(url: str) -> str:
    try:
        return _clean_host(urlsplit(url).hostname)
    except (TypeError, ValueError, DiscoveryInputError):
        raise _DiscoveryRejected(DiscoveryFailureReason.INVALID_URL, url) from None


def _system_resolver(host: str, port: int) -> tuple[str, ...]:
    records = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return tuple(dict.fromkeys(cast(str, record[4][0]) for record in records))


def _media_type(content_type: str | None, url: str) -> MediaType:
    normalized = content_type.split(";", 1)[0].strip().lower() if content_type else ""
    media_type = SUPPORTED_CONTENT_TYPES.get(normalized)
    if media_type is None:
        raise _DiscoveryRejected(DiscoveryFailureReason.UNSUPPORTED_MEDIA_TYPE, url)
    return media_type


def _content_length(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except ValueError:
        return None
    return parsed if parsed >= 0 else None


def _html_canonical(content: bytes, *, base_url: str) -> str | None:
    soup = BeautifulSoup(content, "html.parser", parse_only=SoupStrainer("link"))
    for element in soup.find_all("link"):
        if not isinstance(element, Tag):
            continue
        relations = element.get("rel")
        relation_values = (
            tuple(str(item).casefold() for item in relations)
            if isinstance(relations, list)
            else (str(relations).casefold(),)
            if relations is not None
            else ()
        )
        href = element.get("href")
        if "canonical" in relation_values and isinstance(href, str) and href.strip():
            return urljoin(base_url, href.strip())
    return None


def _html_links(content: bytes, *, base_url: str) -> tuple[str, ...]:
    soup = BeautifulSoup(content, "html.parser", parse_only=SoupStrainer("a"))
    links: list[str] = []
    for element in soup.find_all("a"):
        if not isinstance(element, Tag):
            continue
        href = element.get("href")
        if isinstance(href, str) and href.strip():
            links.append(urljoin(base_url, href.strip()))
    return tuple(dict.fromkeys(links))


__all__ = [
    "DiscoveredDocument",
    "DiscoveryFailureReason",
    "DiscoveryInputError",
    "DiscoveryIssue",
    "DiscoveryPolicy",
    "DiscoveryReport",
    "SourceDiscovery",
    "SourceSeed",
    "canonicalize_source_url",
]
