-- T007 rollback for 001_initial_schema.sql.

DROP TABLE IF EXISTS citations;
DROP TABLE IF EXISTS answer_records;
DROP TABLE IF EXISTS student_questions;
DROP TABLE IF EXISTS conversation_sessions;
DROP TABLE IF EXISTS source_chunks;
DROP TABLE IF EXISTS source_documents;
DROP TABLE IF EXISTS escalation_destinations;
DROP TABLE IF EXISTS corpus_builds;

DROP TYPE IF EXISTS answer_type;
DROP TYPE IF EXISTS corpus_build_status;
DROP TYPE IF EXISTS ingestion_status;
DROP TYPE IF EXISTS source_status;
DROP TYPE IF EXISTS source_type;

DROP EXTENSION IF EXISTS vector;
DROP EXTENSION IF EXISTS pgcrypto;