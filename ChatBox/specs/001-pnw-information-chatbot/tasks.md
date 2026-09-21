# Implementation Tasks: PNW Student Information Chatbot

**Feature**: `001-pnw-information-chatbot`  
**Source**: `spec.md`, `plan.md`, `data-model.md`, `contracts/chat-api.md`

## Task Conventions

- `[P]` means the task can be completed in parallel with other `[P]` tasks in the same phase.
- User-story labels identify the primary acceptance flow: `[US1]`, `[US2]`, or `[US3]`.
- Paths are target paths from the implementation plan; create them as part of the setup tasks where needed.

## Phase 1: Project Setup

- [x] T001 Create the backend Python 3.12 project, dependency configuration, and pytest layout in `backend/pyproject.toml`, `backend/requirements.txt`, and `backend/tests/`.
- [x] T002 [P] Create the React/Vite TypeScript project, package scripts, and test layout in `frontend/package.json`, `frontend/src/`, and `frontend/tests/`.
- [x] T003 [P] Create Docker Compose services, environment templates, health checks, and persistent PostgreSQL/pgvector configuration in `compose.yaml`, `.env.example`, and service Dockerfiles.
- [x] T004 [P] Add shared formatting, linting, type-checking, and test commands for backend and frontend in the project configuration files.
- [x] T005 [P] Document local prerequisites, service startup, model initialization, and the implementation test commands in `README.md` and `specs/001-pnw-information-chatbot/quickstart.md`.

## Phase 2: Foundational Data and Platform

- [ ] T006 Define SQLAlchemy models and enums for `SourceDocument`, `SourceChunk`, `CorpusBuild`, `ConversationSession`, `StudentQuestion`, `AnswerRecord`, `Citation`, and `EscalationDestination` in `backend/app/models/`.
- [ ] T007 Create PostgreSQL migrations for pgvector extension, source metadata, corpus versions, citations, escalation destinations, and ephemeral session state in `backend/app/db/migrations/`.
- [ ] T008 [P] Implement database session management, configuration loading, and startup health checks in `backend/app/core/config.py` and `backend/app/db/`.
- [ ] T009 [P] Define Pydantic request and response schemas matching `contracts/chat-api.md` in `backend/app/schemas/`.
- [ ] T010 [P] Add the curated PNW source manifest and source ownership/review metadata format in `backend/ingestion/sources.yaml`.
- [ ] T011 Implement corpus build state transitions, active-corpus selection, and rollback-safe promotion rules in `backend/app/services/corpus_service.py`.
- [ ] T012 Implement embedding-provider configuration with model name, version, dimensions, and a local Ollama-compatible adapter in `backend/app/services/embedding_service.py`.
- [ ] T013 [P] Add unit tests for model constraints, corpus promotion guards, embedding-dimension checks, and ephemeral-session expiry in `backend/tests/unit/`.

## Phase 3: RAG Corpus Preparation

- [ ] T014 Implement bounded source fetching, content-type validation, canonical URL handling, and content hashing in `backend/ingestion/fetch_sources.py`.
- [ ] T015 [P] Implement HTML extraction that preserves headings, lists, tables, links, and update metadata while excluding navigation and decorative content in `backend/ingestion/extract_content.py`.
- [ ] T016 [P] Implement PDF/document extraction with page references and malformed-document reporting in `backend/ingestion/extract_content.py`.
- [ ] T017 Implement structure-preserving chunking that keeps table rows, prerequisites, dates, conditions, and procedural steps together in `backend/ingestion/chunk_content.py`.
- [ ] T018 Implement source status and freshness review rules, including `active`, `archived`, and `disputed` handling, in `backend/app/services/source_governance.py`.
- [ ] T019 Implement idempotent embedding generation and versioned pgvector writes for validated chunks in `backend/ingestion/embed_and_index.py`.
- [ ] T020 Implement corpus integrity and representative retrieval validation for citation coverage, scope filtering, no-match behavior, and conflicting sources in `backend/ingestion/validate_corpus.py`.
- [ ] T021 Add the corpus build command and failure/rollback behavior so incomplete builds cannot become the active retrieval target in `backend/ingestion/`.
- [ ] T022 [P] Add ingestion tests using representative HTML, tables, PDFs, changed content, unavailable sources, and conflicting source metadata in `backend/tests/ingestion/`.
- [ ] T023 [P] Add retrieval fixture data covering parking, programs, registration, plans of study, prerequisites, deadlines, academic integrity, accessibility, and student services in `backend/tests/fixtures/`.

## Phase 4: Retrieval and Policy Foundation

- [ ] T024 Implement query normalization, embedding lookup, pgvector similarity search, and active corpus filtering by campus, term, status, and freshness in `backend/app/services/retrieval_service.py`.
- [ ] T025 Implement intent and scope extraction for campus, academic term, program, course, student level, and personal-record requests in `backend/app/services/policy_service.py`.
- [ ] T026 Implement evidence sufficiency, stale-source, and materially conflicting-source checks before answer generation in `backend/app/services/policy_service.py`.
- [ ] T027 Implement citation assembly that links every supported answer to direct source URLs and evidence snippets in `backend/app/services/citation_service.py`.
- [ ] T028 Implement Ollama answer drafting with an evidence-only prompt boundary and draft support validation in `backend/app/services/answer_service.py`.
- [ ] T029 [P] Implement approved escalation destination lookup and safe limitation messages in `backend/app/services/escalation_service.py`.
- [ ] T030 [P] Add unit tests for retrieval filters, evidence sufficiency, prompt boundaries, citation requirements, and safe-failure classification in `backend/tests/unit/`.

## Phase 5: User Story 1 - Grounded University Answers (Priority: P1)

**Goal**: A student receives a concise answer with direct official citations, or a focused clarification when campus/term context is required.

- [ ] T031 [US1] Implement `POST /api/chat` request validation, ephemeral session creation, and request orchestration in `backend/app/api/chat.py`.
- [ ] T032 [US1] Implement campus/term/topic context retention, explicit reset behavior, and new-topic scope replacement without transcript persistence in `backend/app/services/session_service.py`.
- [ ] T033 [US1] Return focused follow-up questions before scope-specific retrieval when required campus or term context is missing in `backend/app/services/policy_service.py`.
- [ ] T034 [US1] Render answer text, citations, source links, scope details, follow-up questions, and reset/new-topic actions in `frontend/src/components/ChatView.tsx` and `frontend/src/components/SourceCitation.tsx`.
- [ ] T035 [US1] Add API integration tests for supported answers, multi-source citations, missing campus/term context, session follow-ups, and reset behavior in `backend/tests/api/test_chat.py`.
- [ ] T036 [US1] Add React tests for answer rendering, direct links, clarification prompts, and ephemeral reset behavior in `frontend/tests/chat.test.tsx`.

## Phase 6: User Story 2 - Structured Academic and Procedural Information (Priority: P2)

**Goal**: Answers preserve structured facts and explain procedures distributed across approved pages and documents.

- [ ] T037 [US2] Add retrieval ranking and context assembly that preserves headings, table-row boundaries, page references, prerequisites, dates, and conditions in `backend/app/services/retrieval_service.py`.
- [ ] T038 [US2] Add answer validation rules that reject drafts which merge incompatible campuses, terms, table rows, or student categories in `backend/app/services/answer_service.py`.
- [ ] T039 [US2] Add representative integration tests for deadlines, course prerequisites, program availability, registration procedures, and linked forms in `backend/tests/api/test_structured_answers.py`.
- [ ] T040 [US2] Add frontend rendering tests for structured answer details, source sections, dates, conditions, and procedure links in `frontend/tests/structured-answers.test.tsx`.

## Phase 7: User Story 3 - Safe Unavailable Information Responses (Priority: P1)

**Goal**: Unsupported, conflicting, stale, and personalized questions receive an explicit limitation and an appropriate official referral.

- [ ] T041 [US3] Implement unsupported-topic detection and `insufficient_information` responses without fabricated details in `backend/app/services/policy_service.py`.
- [ ] T042 [US3] Implement conflict and stale-source responses that identify uncertainty and avoid silently selecting a policy in `backend/app/services/policy_service.py`.
- [ ] T043 [US3] Implement personalized-record and binding-decision detection with `escalation` responses in `backend/app/services/policy_service.py`.
- [ ] T044 [US3] Implement `POST /api/chat/reset` and ensure it clears only ephemeral context without storing transcripts in `backend/app/api/chat.py`.
- [ ] T045 [US3] Add API tests for no-match, conflicting sources, stale sources, personalized requests, and escalation destinations in `backend/tests/api/test_safe_failures.py`.
- [ ] T046 [US3] Add frontend tests for limitation messages, escalation links, conflict explanations, and continued conversation after safe failure in `frontend/tests/safe-failures.test.tsx`.

## Phase 8: Integration, Deployment, and Quality Gates

- [ ] T047 Wire FastAPI routes for health, chat, reset, and source search in `backend/app/api/` and register them in `backend/app/main.py`.
- [ ] T048 Implement `GET /api/sources/search` with active-source, campus, and term filters in `backend/app/api/sources.py`.
- [ ] T049 [P] Add end-to-end smoke tests for supported, clarification, structured-answer, and safe-failure flows against Docker Compose in `backend/tests/e2e/`.
- [ ] T050 [P] Add frontend accessibility and responsive-layout checks for the public chat experience in `frontend/tests/`.
- [ ] T051 [P] Add structured logging and operational diagnostics for corpus versions, retrieval outcomes, source freshness, and failures without logging chat transcripts in `backend/app/core/`.
- [ ] T052 Validate p95 retrieval latency, source lookup latency, citation coverage, unsupported-claim rate, and representative-question success criteria using the corpus validation harness in `backend/ingestion/validate_corpus.py`.
- [ ] T053 Run the complete backend, frontend, ingestion, API-contract, and Docker smoke-test suites and record the results in the feature documentation.
- [ ] T054 Update `README.md`, `quickstart.md`, and deployment configuration with the final source refresh, corpus promotion, model initialization, rollback, and troubleshooting procedures.

## Dependencies and Execution Order

1. Complete Phase 1 before implementation work.
2. Complete Phase 2 database, schemas, configuration, and corpus-build foundations before Phases 3-4.
3. Complete Phase 3 corpus preparation before validating retrieval or running supported-answer flows.
4. Complete Phase 4 retrieval and policy services before Phases 5-7 API behavior.
5. Phases 5 and 6 can proceed in parallel after Phase 4; Phase 7 can proceed in parallel once the policy foundation is available.
6. Complete Phase 8 after the user-story flows are integrated.

## Requirement Coverage

- FR-002 to FR-004, FR-018: T024, T027, T028, T031, T034
- FR-005, FR-011 to FR-015: T018, T026, T029, T041-T046
- FR-006 to FR-010, FR-016 to FR-017, FR-025: T014-T023, T037-T040
- FR-008, FR-009, FR-023, FR-024: T032-T036, T044
- FR-019: T023, T039, T045, T052
- SC-001 to SC-007: T020, T035-T036, T039-T040, T045-T046, T049-T053
