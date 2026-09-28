# Tasks: PNW Student Information Chatbot

**Input**: Design documents from `/specs/001-pnw-student-chatbot/`

**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/`, and `quickstart.md`

## Phase 1: Setup

- [X] T001 Create the backend, frontend, deploy, migration, fixture, and test directories from `plan.md`.
- [X] T002 Initialize the Python 3.12 backend with FastAPI, Pydantic 2, SQLAlchemy 2, psycopg 3, Alembic, google-genai, Beautiful Soup, pypdf, and Sentence Transformers in `backend/pyproject.toml`.
- [X] T003 [P] Initialize the React 19 TypeScript/Vite frontend and test scripts in `frontend/package.json` and `frontend/tsconfig.json`.
- [X] T004 [P] Configure Python linting, formatting, typing, and pytest in `backend/pyproject.toml` and `backend/pytest.ini`.
- [X] T005 [P] Configure frontend linting, formatting, typing, and testing in `frontend/package.json`.
- [X] T006 [P] Add credential-free development and production configuration templates in `deploy/.env.example`.
- [X] T007 [P] Create API, web, PostgreSQL/pgvector, migration, ingestion, and optional local-inference Docker services in `deploy/Dockerfile.api`, `deploy/Dockerfile.web`, and `deploy/compose.yaml`.
- [X] T008 Configure Docker health checks, private networks, file-mounted secrets, read-only model mounts, non-root users, and chat-log redaction in `deploy/compose.yaml`, `deploy/compose.gpu.yaml`, and `deploy/proxy.conf`.

## Phase 2: Foundational

- [X] T009 Enable pgvector and create the initial migration configuration in `backend/alembic.ini` and `backend/migrations/versions/001_enable_pgvector.py`.
- [X] T010 [P] Implement `Source`, `SourceVersion`, `Qualification`, `Applicability`, `EvidenceBlock`, `Embedding`, `CourseRelation`, `OfficeReferral`, `Conflict`, `SourceEvent`, `IngestionRun`, and `AggregateMetric` models in `backend/app/models/` with UUIDs, timezone-aware timestamps, and the constraints in `data-model.md`.
- [X] T011 Create PostgreSQL constraints and indexes for canonical URLs, content hashes, qualification freshness, applicability filters, parent evidence, `vector(384)`, and aggregate-only metrics in `backend/migrations/versions/002_source_schema.py`.
- [X] T012 [P] Add typed settings for database, Gemini model/API-key secret path, provider mode, source bounds, quotas, limits, and 24-hour qualification expiry in `backend/app/config.py`.
- [X] T013 [P] Create FastAPI startup, routing, dependency injection, safe error envelopes, request limits, origin validation, and no-store headers in `backend/app/main.py` and `backend/app/api/`.
- [X] T014 [P] Implement bounded aggregate metrics without message text, identifiers, URLs, IP addresses, or query strings in `backend/app/metrics/service.py`.
- [X] T015 Implement in-memory sessions with opaque 256-bit tokens, generation locks, cancellation handles, End chat, and 30-minute student-message-only expiry in `backend/app/sessions/`.
- [X] T016 Implement normalized MiniLM embeddings, temporary query-vector cleanup, and the 220-wordpiece evidence-unit limit in `backend/app/retrieval/embeddings.py`.
- [X] T017 Implement the provider-neutral LLM adapter and structured answer schema in `backend/app/generation/adapter.py` and `backend/app/generation/schemas.py`.
- [X] T018 Implement stateless Gemini `gemini-2.5-flash-lite` calls with structured output, 512-token output bound, no provider conversation IDs/caching, and Docker-secret API keys in `backend/app/generation/gemini.py`.
- [X] T019 [P] Implement the optional local llama.cpp fallback and provider cleanup/quarantine hooks in `backend/app/generation/local.py`.
- [X] T020 Implement quota, timeout, invalid-payload, data-policy, and fallback errors as safe limitations or sanitized 503 responses in `backend/app/generation/errors.py`.
- [X] T021 Implement exact pgvector plus PostgreSQL full-text retrieval over eligible, fresh, applicable, non-conflicting evidence in `backend/app/retrieval/search.py`.
- [X] T022 Implement citation, evidence-ID, date, table, prerequisite, scope, and unsupported-claim validation in `backend/app/generation/validator.py`.
- [X] T023 Add foundational tests for migrations, API envelopes, session expiry, provider isolation, no query-vector persistence, and database-role boundaries in `backend/tests/foundation/`.

## Phase 3: User Story 1 - Get a Supported Answer and Next Steps (P1 MVP)

**Independent test**: Load a synthetic qualifying procedure spanning HTML, a linked page, and a PDF; ask through the API/UI; verify one coherent answer with ordered steps and citations.

- [ ] T024 [P] [US1] Implement typed session/message schemas matching `contracts/http-api.md` in `backend/app/api/schemas.py`.
- [ ] T025 [US1] Implement `POST /api/v1/sessions`, `GET /api/v1/session`, `DELETE /api/v1/session`, and `POST /api/v1/messages` in `backend/app/api/routes.py`.
- [ ] T026 [US1] Implement bounded prompt construction using only session context, eligible evidence IDs/content, and citation instructions in `backend/app/generation/prompt.py`.
- [ ] T027 [P] [US1] Build the React chat page, composer, loading state, answer segments, citation links, and clarification/error rendering in `frontend/src/chat/`.
- [ ] T028 [P] [US1] Implement the typed same-origin frontend client with no persistent browser storage or automatic cross-session retry in `frontend/src/api/client.ts`.
- [ ] T029 [US1] Add End chat, expiry, visibility/resume cleanup, and session-ended messaging in `frontend/src/chat/session.ts` and `frontend/src/chat/ChatPage.tsx`.
- [ ] T030 [US1] Add synthetic multi-document procedure fixtures in `backend/tests/fixtures/corpus/us1/`.
- [ ] T031 [US1] Add supported-answer contract/integration tests for ordered steps, complete evidence, source citations, and partial supported/unknown claims in `backend/tests/contract/test_messages.py` and `backend/tests/integration/test_supported_answers.py`.
- [ ] T032 [US1] Add Playwright coverage for submitting a question, reading citations, and ending a chat in `frontend/tests/e2e/supported-answer.spec.ts`.

## Phase 4: User Story 2 - Fail Safely and Refer Correctly (P1)

**Independent test**: Exercise missing, stale, quarantined, conflicting, unavailable, personal-case, and Gemini-failure fixtures; verify no unsupported claim or invented contact.

- [ ] T033 [P] [US2] Implement reason-coded limitations and eligible office-referral assembly in `backend/app/generation/safe_failure.py`.
- [ ] T034 [P] [US2] Add adverse-source fixtures in `backend/tests/fixtures/corpus/us2/`.
- [ ] T035 [US2] Add integration tests for unsupported claims, conflicts, missing referrals, personal cases, source outages, Gemini 429s, timeouts, and malformed responses in `backend/tests/integration/test_safe_failure.py` and `backend/tests/integration/test_llm_failures.py`.
- [ ] T036 [US2] Add frontend limitation, referral, provider-outage, retry, and no-automatic-resubmission states in `frontend/src/chat/FailureMessage.tsx`.
- [ ] T037 [US2] Add Playwright coverage for safe limitations, verified referrals, provider failures, and personal-case boundaries in `frontend/tests/e2e/failure-behavior.spec.ts`.

## Phase 5: User Story 3 - Get the Right Context (P1)

**Independent test**: Load contrasting campus and term fixtures, omit context, correct it in a follow-up, and verify selected evidence and deadline relationships.

- [ ] T038 [P] [US3] Implement campus, term, year, session, program, student-level, and catalog-year context models and validation in `backend/app/context/`.
- [ ] T039 [US3] Implement context-required responses and corrected-context requery behavior in `backend/app/retrieval/context_gate.py`.
- [ ] T040 [P] [US3] Add table, deadline, campus, term, session, and prerequisite fixtures in `backend/tests/fixtures/corpus/us3/`.
- [ ] T041 [US3] Add integration tests for missing context, term mismatch, past deadlines, refund rows, corrections, and prerequisite relationships in `backend/tests/integration/test_context_retrieval.py`.
- [ ] T042 [US3] Add context prompts, selected-context indicators, correction controls, and past-deadline labels in `frontend/src/chat/ContextPrompt.tsx` and `frontend/src/chat/AnswerContext.tsx`.
- [ ] T043 [US3] Add Playwright coverage for clarification, correction, table citations, and context continuity in `frontend/tests/e2e/context.spec.ts`.

## Phase 6: User Story 4 - Explain Academic Requirements (P2)

**Independent test**: Load catalog fixtures with AND/OR prerequisites and plan-of-study steps; verify preserved relationships and no personal eligibility claims.

- [ ] T044 [P] [US4] Implement prerequisite parsing and retrieval preserving AND/OR groups, grades, corequisites, campus, and catalog year in `backend/app/academic/prerequisites.py`.
- [ ] T045 [P] [US4] Implement program, graduate-admission, plan-of-study, graduation, and current-availability scope rules in `backend/app/academic/programs.py`.
- [ ] T046 [US4] Add catalog, prerequisite, plan-of-study, graduation, incomplete, and typical-offering fixtures in `backend/tests/fixtures/corpus/us4/`.
- [ ] T047 [US4] Add integration tests for program existence, prerequisite summaries, plans, catalog scope, and no-current-availability inference in `backend/tests/integration/test_academic_guidance.py`.
- [ ] T048 [US4] Add prerequisite-group, source-condition, advisor-boundary, and referral presentation in `frontend/src/chat/AcademicAnswer.tsx`.
- [ ] T049 [US4] Add Playwright coverage for prerequisite and plan-of-study explanations in `frontend/tests/e2e/academic-guidance.spec.ts`.

## Phase 7: User Story 5 - Qualify and Track RAG Sources (P1)

**Independent test**: Ingest a qualifying source without a reviewer, reject failed/incomplete sources, load vectors, expire/requalify content, and verify retrieval eligibility.

- [ ] T050 [P] [US5] Implement bounded source discovery, redirect/private-network checks, crawl depth 3, 100-URL/run, 20 MiB/document, and canonical URLs in `backend/app/ingestion/discovery.py`.
- [ ] T051 [P] [US5] Implement HTML/PDF extraction preserving headings, links, page anchors, tables, rows, footnotes, campus labels, and catalog context in `backend/app/ingestion/extract.py`.
- [ ] T052 [US5] Implement immutable versions, content hashes, extraction states, and reason-coded quarantine in `backend/app/ingestion/versions.py`.
- [ ] T053 [US5] Implement automated provenance, completeness, applicability, effective-date, freshness, and conflict qualification in `backend/app/ingestion/qualification.py`.
- [ ] T054 [US5] Implement semantic chunking at headings, complete table rows, and prerequisite-group boundaries with parent/version IDs in `backend/app/ingestion/chunking.py`.
- [ ] T055 [US5] Implement transactional normalized MiniLM embedding and pgvector loading with recorded model revision/dimensions in `backend/app/ingestion/indexer.py`.
- [ ] T056 [US5] Implement exact vector/full-text/structured retrieval, metadata filters, rank fusion, and maximum-eight evidence selection in `backend/app/retrieval/search.py`.
- [ ] T057 [US5] Implement daily refresh, 24-hour expiry, material-change requalification, withdrawal, supersession, and final answer-authorization locking in `backend/app/ingestion/refresh.py` and `backend/app/retrieval/authorization.py`.
- [ ] T058 [P] [US5] Implement source inspect, qualify, withdraw, restore, refresh-due, and evaluation commands in `backend/app/cli.py` matching `contracts/source-operations.md`.
- [ ] T059 [US5] Add linked-page, PDF, malformed-table, duplicate, campus, prerequisite, stale, and conflict fixtures in `backend/tests/fixtures/corpus/ingestion/`.
- [ ] T060 [US5] Add full vector-preparation integration tests for discover, fetch, hash, extract, qualify, chunk, embed, transactional load, validate, publish, refresh, expire, and requalify in `backend/tests/integration/test_vector_pipeline.py`.
- [ ] T061 [US5] Add retrieval authorization tests for qualification filters, exact pgvector, full-text fusion, supersession, withdrawal during generation, and no query-vector persistence in `backend/tests/integration/test_retrieval_authorization.py`.
- [ ] T062 [US5] Add CLI contract tests for bounds, reason codes, idempotent imports, failed refresh, withdrawal, and restore in `backend/tests/contract/test_source_operations.py`.

## Phase 8: Polish and Cross-Cutting Validation

- [ ] T063 [P] Add keyboard, focus, labels, live announcements, contrast, 320px reflow, and screen-reader coverage in `frontend/tests/accessibility/chat-a11y.spec.ts`.
- [ ] T064 [P] Add End chat, 30-minute expiry, browser resume, delayed provider response, logs, caches, Docker layers, and no-persistent-conversation tests in `backend/tests/integration/test_retention.py` and `frontend/tests/e2e/retention.spec.ts`.
- [ ] T065 [P] Add load tests for 200 active sessions, four concurrent generations, 50,000 evidence blocks, 9-second timeout, and Gemini quota backpressure in `tests/load/locustfile.py`.
- [ ] T066 [P] Add provider-policy tests blocking unpaid Gemini production mode unless its data-handling gate is explicitly satisfied in `backend/tests/integration/test_provider_policy.py`.
- [ ] T067 Harden Docker secrets, logging, read-only filesystems, networks, health checks, migrations, backups, and source-event replay in `deploy/compose.yaml`, `deploy/compose.gpu.yaml`, and `deploy/proxy.conf`.
- [ ] T068 Run every scenario in `specs/001-pnw-student-chatbot/quickstart.md`, record results in `docs/validation-report.md`, and block release on failed SC-001–SC-008 criteria.
- [ ] T069 Update setup, Gemini configuration, source qualification, RAG preparation, retention, fallback, and troubleshooting documentation in `README.md` and `docs/operations.md`.

## Dependencies and Execution Order

- Setup (Phase 1) has no dependencies.
- Foundational (Phase 2) depends on Setup and blocks all stories.
- US1, US2, and US3 can proceed in parallel after Phase 2 using synthetic evidence.
- US4 depends on shared context handling and academic fixtures but remains independently testable.
- US5 depends on the database foundation and provides the production RAG corpus; synthetic fixtures permit earlier story work.
- Polish depends on the stories selected for release.

## Parallel Opportunities

- T003–T008 can run in parallel after T001–T002.
- T012–T014, T016–T020, and T023 can run in parallel after the schema baseline.
- US1, US2, and US3 can be assigned to separate developers after Phase 2.
- US5 discovery/extraction/chunking, embedding/indexing, and CLI work can proceed in parallel once the model schema is stable.
- T063–T067 can run in parallel during Polish.

## MVP Strategy

1. Complete Setup and Foundational phases.
2. Complete US1 against synthetic qualified evidence.
3. Validate direct answers, citations, Gemini/fallback behavior, and retention.
4. Add US2 and US3 before exposing the real PNW corpus.
5. Complete US5 to prepare the automated RAG vector database, then add US4 and run release validation.

All tasks follow the required checklist format: checkbox, sequential ID, optional `[P]`, required
story label for story tasks, and an exact file path.
