from __future__ import annotations

from tests.fixtures.retrieval import (
    RETRIEVAL_CASES,
    RETRIEVAL_FIXTURES,
    get_retrieval_fixture_dataset,
)


def test_retrieval_fixtures_cover_expected_topics() -> None:
    sources, cases = get_retrieval_fixture_dataset()

    assert len(RETRIEVAL_FIXTURES) >= 9
    assert len(sources) == len(RETRIEVAL_FIXTURES)
    assert len(cases) == len(RETRIEVAL_CASES)

    required_topics = {
        "parking",
        "programs",
        "registration",
        "plans of study",
        "prerequisites",
        "deadlines",
        "academic integrity",
        "accessibility",
        "student services",
    }

    source_topics = {source.title.lower() for source in sources}
    assert required_topics.issubset(source_topics)

    assert all(case.name for case in cases)
