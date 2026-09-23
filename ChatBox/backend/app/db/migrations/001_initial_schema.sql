-- T007: PostgreSQL schema for the source corpus and ephemeral chat state.
-- Run this migration once against the configured PostgreSQL database.

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

DO $$
BEGIN
    CREATE TYPE source_type AS ENUM (
        'webpage', 'pdf', 'handbook', 'catalog', 'policy'
    );
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

DO $$
BEGIN
    CREATE TYPE source_status AS ENUM ('active', 'archived', 'disputed');
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

DO $$
BEGIN
    CREATE TYPE ingestion_status AS ENUM (
        'discovered', 'extracted', 'embedded', 'validated', 'failed'
    );
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

DO $$
BEGIN
    CREATE TYPE corpus_build_status AS ENUM (
        'building', 'validated', 'promoted', 'failed', 'retired'
    );
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

DO $$
BEGIN
    CREATE TYPE answer_type AS ENUM (
        'supported', 'insufficient_information', 'conflict', 'escalation'
    );
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

CREATE TABLE corpus_builds (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    version VARCHAR(100) NOT NULL UNIQUE,
    embedding_model VARCHAR(255) NOT NULL,
    embedding_dimensions INTEGER NOT NULL CHECK (embedding_dimensions > 0),
    source_count INTEGER NOT NULL DEFAULT 0 CHECK (source_count >= 0),
    chunk_count INTEGER NOT NULL DEFAULT 0 CHECK (chunk_count >= 0),
    status corpus_build_status NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,
    validation_summary TEXT
);

CREATE TABLE escalation_destinations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL CHECK (length(name) > 0),
    category VARCHAR(100) NOT NULL CHECK (length(category) > 0),
    url TEXT NOT NULL CHECK (length(url) > 0),
    email VARCHAR(255),
    phone VARCHAR(50),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE source_documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title TEXT NOT NULL,
    url TEXT NOT NULL UNIQUE CHECK (length(url) > 0),
    source_type source_type NOT NULL,
    campus_scope VARCHAR(100),
    academic_term VARCHAR(100),
    last_updated DATE,
    status source_status NOT NULL,
    reviewed_by VARCHAR(255),
    content_hash VARCHAR(128) NOT NULL,
    ingestion_status ingestion_status NOT NULL,
    corpus_version VARCHAR(100) REFERENCES corpus_builds(version),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE source_chunks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID NOT NULL REFERENCES source_documents(id),
    chunk_index INTEGER NOT NULL CHECK (chunk_index >= 1),
    heading TEXT,
    content TEXT NOT NULL CHECK (length(content) > 0),
    page_ref VARCHAR(100),
    embedding vector,
    embedding_model VARCHAR(255),
    embedding_dimensions INTEGER CHECK (embedding_dimensions IS NULL OR embedding_dimensions > 0),
    corpus_version VARCHAR(100) REFERENCES corpus_builds(version),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE conversation_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_token VARCHAR(255) NOT NULL UNIQUE,
    campus_context VARCHAR(100),
    term_context VARCHAR(100),
    topic_scope TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE student_questions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES conversation_sessions(id),
    raw_text TEXT NOT NULL CHECK (length(raw_text) > 0),
    normalized_text TEXT NOT NULL,
    campus_hint VARCHAR(100),
    term_hint VARCHAR(100),
    user_intent VARCHAR(100),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE answer_records (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES conversation_sessions(id),
    question_id UUID NOT NULL UNIQUE REFERENCES student_questions(id),
    answer_text TEXT NOT NULL CHECK (length(answer_text) > 0),
    answer_type answer_type NOT NULL,
    confidence DOUBLE PRECISION,
    has_direct_link BOOLEAN NOT NULL DEFAULT FALSE,
    escalation_target TEXT,
    escalation_destination_id UUID REFERENCES escalation_destinations(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE citations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    answer_id UUID NOT NULL REFERENCES answer_records(id),
    document_id UUID NOT NULL REFERENCES source_documents(id),
    chunk_id UUID NOT NULL REFERENCES source_chunks(id),
    quote_snippet TEXT NOT NULL CHECK (length(quote_snippet) > 0),
    link_url TEXT NOT NULL CHECK (length(link_url) > 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX one_promoted_corpus_build
    ON corpus_builds (status)
    WHERE status = 'promoted';

CREATE INDEX source_chunks_document_index
    ON source_chunks (document_id, chunk_index);

CREATE INDEX source_documents_scope_index
    ON source_documents (status, campus_scope, academic_term);

CREATE INDEX conversation_sessions_expiry_index
    ON conversation_sessions (expires_at);