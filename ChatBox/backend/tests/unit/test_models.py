from datetime import datetime, timedelta, timezone

from app.models import ConversationSession, SourceChunk, SourceDocument


def constraint_names(model: type[object]) -> set[str]:
    table = model.__table__  # type: ignore[attr-defined]
    return {constraint.name for constraint in table.constraints if constraint.name}


def test_source_models_define_required_content_constraints() -> None:
    assert "ck_source_chunk_content_nonempty" in constraint_names(SourceChunk)
    assert "ck_source_chunk_index_positive" in constraint_names(SourceChunk)
    assert "ck_source_document_url_nonempty" in constraint_names(SourceDocument)


def test_session_expiry_is_required_and_can_be_compared() -> None:
    expiry_column = ConversationSession.__table__.c.expires_at
    assert expiry_column.nullable is False

    now = datetime.now(timezone.utc)
    session = ConversationSession(
        session_token="test-session",
        created_at=now,
        expires_at=now - timedelta(seconds=1),
    )
    assert session.expires_at < now