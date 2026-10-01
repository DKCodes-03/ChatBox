import hashlib

import httpx
import pytest

from ingestion.fetch_sources import (
    SourceFetcher,
    SourceFetchError,
    SourceTooLargeError,
    UnsafeSourceURLError,
    UnsupportedContentTypeError,
)


@pytest.mark.asyncio
async def test_fetch_canonicalizes_url_and_hashes_content() -> None:
    content = b"official source"

    async def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://pnw.edu/registrar/"
        return httpx.Response(
            200, headers={"content-type": "text/html; charset=utf-8"}, content=content
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = await SourceFetcher(client, allowed_hosts={"pnw.edu"}).fetch(
            "HTTPS://PNW.EDU/registrar/#contact"
        )

    assert source.canonical_url == "https://pnw.edu/registrar/"
    assert source.content_type == "text/html"
    assert source.content == content
    assert source.content_hash == hashlib.sha256(content).hexdigest()


@pytest.mark.asyncio
async def test_fetch_normalizes_html_before_hashing() -> None:
    content = "cafe\u0301\r\nregistrar".encode("utf-8")
    normalized = "café\nregistrar".encode()

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            content=content,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = await SourceFetcher(client, allowed_hosts={"pnw.edu"}).fetch(
            "https://pnw.edu/registrar"
        )

    assert source.content == normalized
    assert source.content_hash == hashlib.sha256(normalized).hexdigest()


@pytest.mark.asyncio
async def test_fetch_rejects_disallowed_host_before_request() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        pytest.fail("disallowed URL should not be requested")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        fetcher = SourceFetcher(client, allowed_hosts={"pnw.edu"})
        with pytest.raises(UnsafeSourceURLError):
            await fetcher.fetch("https://example.com/page")


@pytest.mark.asyncio
async def test_fetch_rejects_redirect_outside_allowlist() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://example.com/redirected"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        fetcher = SourceFetcher(client, allowed_hosts={"pnw.edu"})
        with pytest.raises(UnsafeSourceURLError):
            await fetcher.fetch("https://pnw.edu/start")


@pytest.mark.asyncio
async def test_fetch_follows_allowed_redirect() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "/registrar/"})
        return httpx.Response(200, headers={"content-type": "text/html"}, content=b"page")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = await SourceFetcher(client, allowed_hosts={"pnw.edu"}).fetch(
            "https://pnw.edu/start"
        )

    assert source.canonical_url == "https://pnw.edu/registrar/"
    assert source.content == b"page"


@pytest.mark.asyncio
async def test_fetch_preserves_trailing_slash_redirect_target() -> None:
    requested_paths: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requested_paths.append(request.url.path)
        if request.url.path == "/registrar":
            return httpx.Response(301, headers={"location": "/registrar/"})
        return httpx.Response(200, headers={"content-type": "text/html"}, content=b"page")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = await SourceFetcher(client, allowed_hosts={"pnw.edu"}).fetch(
            "https://pnw.edu/registrar"
        )

    assert requested_paths == ["/registrar", "/registrar/"]
    assert source.canonical_url == "https://pnw.edu/registrar/"


@pytest.mark.asyncio
async def test_fetch_enforces_redirect_limit() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "/next"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        fetcher = SourceFetcher(client, allowed_hosts={"pnw.edu"}, max_redirects=1)
        with pytest.raises(SourceFetchError, match="redirect limit"):
            await fetcher.fetch("https://pnw.edu/start")


@pytest.mark.asyncio
async def test_fetch_wraps_request_timeout() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("request timed out", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        fetcher = SourceFetcher(client, allowed_hosts={"pnw.edu"})
        with pytest.raises(SourceFetchError, match="Failed to fetch source"):
            await fetcher.fetch("https://pnw.edu/page")


@pytest.mark.asyncio
async def test_fetch_rejects_unsupported_content_type() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "application/json"}, content=b"{}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        fetcher = SourceFetcher(client, allowed_hosts={"pnw.edu"})
        with pytest.raises(UnsupportedContentTypeError):
            await fetcher.fetch("https://pnw.edu/data")


@pytest.mark.asyncio
async def test_fetch_enforces_streamed_byte_limit() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/html"}, content=b"12345")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        fetcher = SourceFetcher(client, allowed_hosts={"pnw.edu"}, max_bytes=4)
        with pytest.raises(SourceTooLargeError):
            await fetcher.fetch("https://pnw.edu/page")


@pytest.mark.asyncio
async def test_fetch_detects_missing_source_and_content_changes() -> None:
    content_versions = iter(
        [
            b"updated registration deadline is August 1",
            b"updated registration deadline is August 15",
        ]
    )

    async def handler(_request: httpx.Request) -> httpx.Response:
        if _request.url.path == "/missing":
            return httpx.Response(404, headers={"content-type": "text/html"}, content=b"Not found")
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            content=next(content_versions),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        fetcher = SourceFetcher(client, allowed_hosts={"pnw.edu"})

        with pytest.raises(SourceFetchError, match="HTTP 404"):
            await fetcher.fetch("https://pnw.edu/missing")

        first = await fetcher.fetch("https://pnw.edu/registration")
        second = await fetcher.fetch("https://pnw.edu/registration")

    assert first.content_hash != second.content_hash
    assert first.content != second.content
    assert b"August 1" in first.content
    assert b"August 15" in second.content
