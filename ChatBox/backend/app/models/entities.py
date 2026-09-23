from __future__ import annotations

import enum
from datetime import date, datetime
from uuid import UUID

from pgvector.sqlalchemy import Vector
from sqlalchemy import Boolean, CheckConstraint, Enum, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, CreatedAt, UUIDPrimaryKey


class SourceType(str, enum.Enum):
    WEBPAGE = "webpage"
    PDF = "pdf"
    HANDBOOK = "handbook"
    CATALOG = "catalog"
    POLICY = "policy"


class SourceStatus(str, enum.Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"
    DISPUTED = "disputed"


class IngestionStatus(str, enum.Enum):
    DISCOVERED = "discovered"
    EXTRACTED = "extracted"
    EMBEDDED = "embedded"
    VALIDATED = "validated"
    FAILED = "failed"


class CorpusBuildStatus(str, enum.Enum):
    BUILDING = "building"
    VALIDATED = "validated"
    PROMOTED = "promoted"
    FAILED = "failed"
    RETIRED = "retired"


class AnswerType(str, enum.Enum):
    SUPPORTED = "supported"
    INSUFFICIENT_INFORMATION = "insufficient_information"
    CONFLICT = "conflict"
    ESCALATION = "escalation"


def enum_values(enum_class: type[enum.Enum]) -> list[str]:
    return [member.value for member in enum_class]


class CorpusBuild(Base):
    __tablename__ = "corpus_builds"

    id: Mapped[UUIDPrimaryKey]
    version: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    embedding_model: Mapped[str] = mapped_column(String(255), nullable=False)
    embedding_dimensions: Mapped[int] = mapped_column(Integer, nullable=False)
    source_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[CorpusBuildStatus] = mapped_column(
        Enum(
            CorpusBuildStatus,
            name="corpus_build_status",
            values_callable=enum_values,
        ),
        nullable=False,
    )
    started_at: Mapped[datetime] = mapped_column(nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    validation_summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    source_documents: Mapped[list[SourceDocument]] = relationship(back_populates="corpus_build")
    source_chunks: Mapped[list[SourceChunk]] = relationship(back_populates="corpus_build")

    __table_args__ = (
        CheckConstraint("embedding_dimensions > 0", name="ck_corpus_build_dimensions_positive"),
        CheckConstraint("source_count >= 0", name="ck_corpus_build_source_count_nonnegative"),
        CheckConstraint("chunk_count >= 0", name="ck_corpus_build_chunk_count_nonnegative"),
    )


class SourceDocument(Base):
    __tablename__ = "source_documents"

    id: Mapped[UUIDPrimaryKey]
    title: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    source_type: Mapped[SourceType] = mapped_column(
        Enum(SourceType, name="source_type", values_callable=enum_values), nullable=False
    )
    campus_scope: Mapped[str | None] = mapped_column(String(100), nullable=True)
    academic_term: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_updated: Mapped[date | None] = mapped_column(nullable=True)
    status: Mapped[SourceStatus] = mapped_column(
        Enum(SourceStatus, name="source_status", values_callable=enum_values), nullable=False
    )
    reviewed_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    ingestion_status: Mapped[IngestionStatus] = mapped_column(
        Enum(IngestionStatus, name="ingestion_status", values_callable=enum_values), nullable=False
    )
    corpus_version: Mapped[str | None] = mapped_column(
        ForeignKey("corpus_builds.version"), nullable=True
    )
    created_at: Mapped[CreatedAt]

    corpus_build: Mapped[CorpusBuild | None] = relationship(back_populates="source_documents")
    chunks: Mapped[list[SourceChunk]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )
    citations: Mapped[list[Citation]] = relationship(back_populates="document")

    __table_args__ = (CheckConstraint("length(url) > 0", name="ck_source_document_url_nonempty"),)


class SourceChunk(Base):
    __tablename__ = "source_chunks"

    id: Mapped[UUIDPrimaryKey]
    document_id: Mapped[UUID] = mapped_column(ForeignKey("source_documents.id"), nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    heading: Mapped[str | None] = mapped_column(Text, nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    page_ref: Mapped[str | None] = mapped_column(String(100), nullable=True)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(), nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    embedding_dimensions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    corpus_version: Mapped[str | None] = mapped_column(
        ForeignKey("corpus_builds.version"), nullable=True
    )
    created_at: Mapped[CreatedAt]

    document: Mapped[SourceDocument] = relationship(back_populates="chunks")
    corpus_build: Mapped[CorpusBuild | None] = relationship(back_populates="source_chunks")
    citations: Mapped[list[Citation]] = relationship(back_populates="chunk")

    __table_args__ = (
        CheckConstraint("chunk_index >= 1", name="ck_source_chunk_index_positive"),
        CheckConstraint("length(content) > 0", name="ck_source_chunk_content_nonempty"),
        CheckConstraint(
            "embedding_dimensions IS NULL OR embedding_dimensions > 0",
            name="ck_source_chunk_dimensions_positive",
        ),
    )


class ConversationSession(Base):
    __tablename__ = "conversation_sessions"

    id: Mapped[UUIDPrimaryKey]
    session_token: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    campus_context: Mapped[str | None] = mapped_column(String(100), nullable=True)
    term_context: Mapped[str | None] = mapped_column(String(100), nullable=True)
    topic_scope: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[CreatedAt]
    expires_at: Mapped[datetime] = mapped_column(nullable=False)

    questions: Mapped[list[StudentQuestion]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )
    answers: Mapped[list[AnswerRecord]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )


class StudentQuestion(Base):
    __tablename__ = "student_questions"

    id: Mapped[UUIDPrimaryKey]
    session_id: Mapped[UUID] = mapped_column(
        ForeignKey("conversation_sessions.id"), nullable=False
    )
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_text: Mapped[str] = mapped_column(Text, nullable=False)
    campus_hint: Mapped[str | None] = mapped_column(String(100), nullable=True)
    term_hint: Mapped[str | None] = mapped_column(String(100), nullable=True)
    user_intent: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[CreatedAt]

    session: Mapped[ConversationSession] = relationship(back_populates="questions")
    answer: Mapped[AnswerRecord | None] = relationship(
        back_populates="question", uselist=False, cascade="all, delete-orphan"
    )

    __table_args__ = (CheckConstraint("length(raw_text) > 0", name="ck_student_question_nonempty"),)


class AnswerRecord(Base):
    __tablename__ = "answer_records"

    id: Mapped[UUIDPrimaryKey]
    session_id: Mapped[UUID] = mapped_column(
        ForeignKey("conversation_sessions.id"), nullable=False
    )
    question_id: Mapped[UUID] = mapped_column(
        ForeignKey("student_questions.id"), unique=True, nullable=False
    )
    answer_text: Mapped[str] = mapped_column(Text, nullable=False)
    answer_type: Mapped[AnswerType] = mapped_column(
        Enum(AnswerType, name="answer_type", values_callable=enum_values), nullable=False
    )
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    has_direct_link: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    escalation_target: Mapped[str | None] = mapped_column(Text, nullable=True)
    escalation_destination_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("escalation_destinations.id"), nullable=True
    )
    created_at: Mapped[CreatedAt]

    session: Mapped[ConversationSession] = relationship(back_populates="answers")
    question: Mapped[StudentQuestion] = relationship(back_populates="answer")
    citations: Mapped[list[Citation]] = relationship(
        back_populates="answer", cascade="all, delete-orphan"
    )
    escalation_destination: Mapped[EscalationDestination | None] = relationship(
        back_populates="answers"
    )

    __table_args__ = (CheckConstraint("length(answer_text) > 0", name="ck_answer_record_nonempty"),)


class Citation(Base):
    __tablename__ = "citations"

    id: Mapped[UUIDPrimaryKey]
    answer_id: Mapped[UUID] = mapped_column(ForeignKey("answer_records.id"), nullable=False)
    document_id: Mapped[UUID] = mapped_column(ForeignKey("source_documents.id"), nullable=False)
    chunk_id: Mapped[UUID] = mapped_column(ForeignKey("source_chunks.id"), nullable=False)
    quote_snippet: Mapped[str] = mapped_column(Text, nullable=False)
    link_url: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[CreatedAt]

    answer: Mapped[AnswerRecord] = relationship(back_populates="citations")
    document: Mapped[SourceDocument] = relationship(back_populates="citations")
    chunk: Mapped[SourceChunk] = relationship(back_populates="citations")

    __table_args__ = (
        CheckConstraint("length(quote_snippet) > 0", name="ck_citation_quote_nonempty"),
        CheckConstraint("length(link_url) > 0", name="ck_citation_url_nonempty"),
    )


class EscalationDestination(Base):
    __tablename__ = "escalation_destinations"

    id: Mapped[UUIDPrimaryKey]
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_at: Mapped[CreatedAt]

    answers: Mapped[list[AnswerRecord]] = relationship(back_populates="escalation_destination")

    __table_args__ = (
        CheckConstraint("length(name) > 0", name="ck_escalation_name_nonempty"),
        CheckConstraint("length(category) > 0", name="ck_escalation_category_nonempty"),
        CheckConstraint("length(url) > 0", name="ck_escalation_url_nonempty"),
    )