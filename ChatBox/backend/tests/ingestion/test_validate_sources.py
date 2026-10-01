import httpx
import pytest

from ingestion.fetch_sources import SourceFetcher
from ingestion.validate_sources import validate_sources


@pytest.mark.asyncio
async def test_validate_sources_checks_pending_urls_and_counts_chunks() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            content=(
                b"<html><head><title>Academic Calendar</title></head><body><main>"
                b"<h1>Registration</h1><p>Registration opens on August 1.</p>"
                b"</main></body></html>"
            ),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        fetcher = SourceFetcher(client, allowed_hosts={"pnw.edu"})
        results = await validate_sources(
            [
                {
                    "id": "pending-calendar",
                    "title": "Calendar",
                    "url": "https://pnw.edu/calendar",
                    "review_status": "pending_initial_review",
                }
            ],
            fetcher,
        )

    assert len(results) == 1
    assert results[0].ok
    assert results[0].content_type == "text/html"
    assert results[0].chunk_count == 1


@pytest.mark.asyncio
async def test_validate_sources_reports_fetch_and_parse_failures_per_link() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/missing":
            return httpx.Response(404, headers={"content-type": "text/html"})
        return httpx.Response(
            200,
            headers={"content-type": "application/pdf"},
            content=b"not a PDF",
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        fetcher = SourceFetcher(client, allowed_hosts={"pnw.edu"})
        results = await validate_sources(
            [
                {"id": "missing", "url": "https://pnw.edu/missing"},
                {"id": "malformed", "url": "https://pnw.edu/malformed.pdf"},
            ],
            fetcher,
        )

    assert [result.ok for result in results] == [False, False]
    assert "HTTP 404" in (results[0].error or "")
    assert "malformed_document" in (results[1].error or "")