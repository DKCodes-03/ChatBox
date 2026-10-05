"""Security and bound checks for public source discovery."""

from __future__ import annotations

from collections.abc import Callable, Iterable

import httpx
import pytest
from app.config import MAX_DOCUMENT_BYTES
from app.ingestion.discovery import (
    DiscoveryFailureReason,
    DiscoveryInputError,
    DiscoveryPolicy,
    DiscoveryReport,
    SourceDiscovery,
    SourceSeed,
    canonicalize_source_url,
)
from app.models.enums import MediaType

PUBLIC_ADDRESS = "8.8.8.8"


def _public_resolver(_host: str, _port: int) -> tuple[str, ...]:
    return (PUBLIC_ADDRESS,)


def _seed(
    url: str = "https://www.pnw.edu/root/",
    *,
    child_hosts: tuple[str, ...] = ("www.pnw.edu", "catalog.pnw.edu"),
) -> SourceSeed:
    return SourceSeed(
        url=url,
        title="PNW source",
        topics=("registration",),
        allowed_child_hosts=child_hosts,
    )


def _discover(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    policy: DiscoveryPolicy | None = None,
    resolver: Callable[[str, int], Iterable[str]] = _public_resolver,
    seed: SourceSeed | None = None,
) -> DiscoveryReport:
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        discovery = SourceDiscovery(
            policy or DiscoveryPolicy(),
            client=client,
            resolver=resolver,
        )
        return discovery.discover((seed or _seed(),))


def _html(
    body: str, *, status_code: int = 200, headers: dict[str, str] | None = None
) -> httpx.Response:
    response_headers = {"content-type": "text/html"}
    response_headers.update(headers or {})
    return httpx.Response(status_code, headers=response_headers, content=body.encode())


def test_default_policy_enforces_the_approved_hard_limits() -> None:
    policy = DiscoveryPolicy()

    assert policy.max_crawl_depth == 3
    assert policy.max_urls_per_run == 100
    assert policy.max_document_bytes == 20 * 1024 * 1024 == MAX_DOCUMENT_BYTES
    assert policy.allowed_schemes == ("https",)

    with pytest.raises(DiscoveryInputError, match="one hundred"):
        DiscoveryPolicy(max_urls_per_run=101)
    with pytest.raises(DiscoveryInputError, match="twenty MiB"):
        DiscoveryPolicy(max_document_bytes=MAX_DOCUMENT_BYTES + 1)


def test_canonical_url_removes_fragment_default_port_and_dot_segments() -> None:
    canonical = canonicalize_source_url(
        "https://WWW.PNW.EDU:443/a/../policy/?term=fall#deadline",
        allowed_hosts=("www.pnw.edu",),
    )

    assert canonical == "https://www.pnw.edu/policy/?term=fall"


def test_discovery_stops_after_depth_three() -> None:
    requested_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_paths.append(request.url.path)
        depth = int(request.url.path.strip("/") or "0")
        return _html(f'<a href="/{depth + 1}">next</a>')

    report = _discover(handler, seed=_seed("https://www.pnw.edu/0"))

    assert requested_paths == ["/0", "/1", "/2", "/3"]
    assert [document.depth for document in report.documents] == [0, 1, 2, 3]
    assert report.truncated is False


def test_discovery_counts_redirects_against_the_one_hundred_request_budget() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/root/":
            links = "".join(f'<a href="/document/{number}">doc</a>' for number in range(120))
            return _html(links)
        return _html("<p>document</p>")

    report = _discover(handler)

    assert len(report.attempted_urls) == 100
    assert len(report.documents) == 100
    assert report.truncated is True


def test_redirect_destination_is_dns_checked_before_it_is_requested() -> None:
    requested_hosts: list[str] = []

    def resolver(host: str, _port: int) -> tuple[str, ...]:
        return ("127.0.0.1",) if host == "catalog.pnw.edu" else (PUBLIC_ADDRESS,)

    def handler(request: httpx.Request) -> httpx.Response:
        requested_hosts.append(request.url.host)
        return httpx.Response(302, headers={"location": "https://catalog.pnw.edu/private"})

    report = _discover(handler, resolver=resolver)

    assert requested_hosts == ["www.pnw.edu"]
    assert report.documents == ()
    assert report.issues[0].reason is DiscoveryFailureReason.NON_PUBLIC_ADDRESS
    assert report.issues[0].url == "https://catalog.pnw.edu/private"


def test_any_private_address_in_a_dns_answer_rejects_the_request() -> None:
    request_was_sent = False

    def resolver(_host: str, _port: int) -> tuple[str, ...]:
        return (PUBLIC_ADDRESS, "10.0.0.1")

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal request_was_sent
        request_was_sent = True
        return _html("<p>must not be fetched</p>")

    report = _discover(handler, resolver=resolver)

    assert request_was_sent is False
    assert report.documents == ()
    assert report.issues[0].reason is DiscoveryFailureReason.NON_PUBLIC_ADDRESS


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(
            200,
            headers={"content-type": "text/html", "content-length": "11"},
            content=b"x" * 11,
        ),
        httpx.Response(
            200,
            headers={"content-type": "application/pdf"},
            stream=httpx.ByteStream(b"x" * 11),
        ),
    ],
    ids=("declared-length", "streamed-length"),
)
def test_document_size_is_checked_before_and_during_read(response: httpx.Response) -> None:
    report = _discover(
        lambda _request: response,
        policy=DiscoveryPolicy(max_document_bytes=10),
    )

    assert report.documents == ()
    assert report.issues[0].reason is DiscoveryFailureReason.DOCUMENT_TOO_LARGE


def test_validated_html_canonical_and_pdf_media_type_are_preserved() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/root/":
            return _html('<link rel="canonical" href="/official"><a href="/form.pdf">form</a>')
        return httpx.Response(
            200,
            headers={"content-type": "application/pdf"},
            content=b"%PDF fixture",
        )

    report = _discover(handler)

    assert [document.canonical_url for document in report.documents] == [
        "https://www.pnw.edu/official",
        "https://www.pnw.edu/form.pdf",
    ]
    assert [document.media_type for document in report.documents] == [
        MediaType.HTML,
        MediaType.PDF,
    ]
