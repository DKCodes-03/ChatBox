# Quickstart and Validation Guide

This is the implementation acceptance guide. Commands below describe files and entry points
that `$speckit-tasks` and implementation must create; they are not runnable in the current
documentation-only repository. Do not interpret this guide as completed test evidence.
Run commands from the repository root. Do not use real student conversations as test input.

## Prerequisites

- Docker Engine and Compose v2; enough disk for PostgreSQL and pinned local model weights.
- CPU profile for functional development; deployment GPU profile for performance qualification.
- Model files and tokenizer at exact pinned revisions, with checksums recorded in the build
  manifest. Provision Qwen3-4B-GGUF Q4_K_M and all-MiniLM-L6-v2 locally before offline runtime.
- Deployment secrets files for database runtime, migration and governance roles. An implementation
  `.env.example` documents paths and nonsecret settings; no credentials in images or tracked files.
- Synthetic HTML, table, PDF, linked-page, catalog and conflict fixtures with expected automated
  qualification outcomes. No office sign-off fixtures or production document approvals are needed.
- A production host without swap/core dumps and with prompt/access logging disabled. A sandbox
  development machine is not evidence of production retention compliance.

## Build and start the synthetic validation environment

Implementation must provide a `test` Compose profile with deterministic inference and a fixture
loader, separate from production model inference. The fake must implement the inference/cleanup
contract and support failure injection. It cannot qualify answer quality or model performance.

```sh
cp deploy/.env.example deploy/.env
# Fill local settings and mount secret files following deploy/.env.example.
docker compose --env-file deploy/.env -f deploy/compose.yaml --profile test build
docker compose --env-file deploy/.env -f deploy/compose.yaml --profile test up -d db
# Migrations enable pgvector and create source/governance tables, never conversation tables.
docker compose --env-file deploy/.env -f deploy/compose.yaml --profile test run --rm migrate
docker compose --env-file deploy/.env -f deploy/compose.yaml --profile test run --rm test-seed
docker compose --env-file deploy/.env -f deploy/compose.yaml --profile test up -d api web inference-fake
curl --fail http://localhost:8080/api/v1/health/ready
```

Expected: readiness 200 after database, eligible synthetic corpus and inference are ready.
The test profile must refuse production credentials and bind to localhost. Open the local web
page, submit a synthetic parking/procedure question, and receive a direct answer with citations.
No fixture should imply that invented test policy is real PNW policy.

## Automated validation commands

Implementation must expose these scripts and test paths with the specified intent:

```sh
docker compose --env-file deploy/.env -f deploy/compose.yaml --profile test run --rm test-backend pytest tests/unit tests/integration tests/contract
docker compose --env-file deploy/.env -f deploy/compose.yaml --profile test run --rm test-web npm run test:unit
docker compose --env-file deploy/.env -f deploy/compose.yaml --profile test run --rm test-web npm run test:e2e
```

Backend tests use real PostgreSQL/pgvector for eligibility and transactional races, not SQLite.
Frontend/browser tests run against the synthetic deployment. Compare FastAPI-generated OpenAPI
with [contracts/http-api.md](contracts/http-api.md). Validate operator behavior against
[contracts/source-operations.md](contracts/source-operations.md) and state transitions against
[data-model.md](data-model.md).

## Required end-to-end scenarios

| Scenario | Action and expected result |
| --- | --- |
| Supported answer | Ask an answerable question in every FR-012 topic; direct accurate explanation, complete steps/conditions, citations supporting every official claim |
| Fragmented procedure | Combine eligible page and linked PDF; one coherent answer, independently eligible citations; an ineligible child causes a partial answer/limitation |
| Context | Omit campus or term where it changes the answer; chatbot asks; correction changes evidence selection without retaining old assumptions |
| Tables and deadlines | Query distinct terms/sessions/refund rows; correct event/date/percentage associations, past-date label, no fabricated deadline |
| Catalog | Exercise AND/OR prerequisites and program inventory; no inference of current availability or nonexistence from missing search results |
| Fail safely | Missing evidence, unavailable source, stale qualification, conflicting policy and unsupported contacts produce honest limitations; no invented office |
| Authority lifecycle | Qualify without an approver, withdraw, expire at 24 hours, requalify and test source-backed supersession; failures block use |
| Withdrawal race | Pause generation, commit source withdrawal, then resume; affected answer is discarded; test reverse lock ordering as well |
| Personal case | Ask to pay a ticket, fix an account, obtain a PIN or decide graduation status; supported general guidance/referral only |
| End chat | Send distinctive synthetic text, start delayed generation, select End chat; clear browser/API state, cancel inference, erase slot, no late response |
| Idle expiry | With an injectable clock test just before and at 30 minutes; only student messages reset the timer; late messages start fresh context |
| Browser resume | Suspend/restore tab after expiry and exercise back navigation; clear expired content before rendering, no storage/cache transcript recovery |
| Dependency failure | Stop database or inference and exceed capacity; fixed safe errors/429, retry guidance, no leakage of bodies/identifiers |
| Model cleanup failure | Simulate failed slot erase; quarantine slot and restart inference, never reuse unconfirmed prompt state |
| Accessibility | Complete chat, citations, clarification and End chat using keyboard and screen reader; test focus, announcements, 320px reflow and timeout warning |

Clock acceleration is test-only and prohibited in production configuration. Real-timer smoke tests
must also verify that expiry cleanup works without any further student request.

## Retention inspection

Use an unmistakable synthetic marker in a message and deliberately trigger validation errors,
model timeouts and crashes. After End chat and expiry, inspect PostgreSQL data and backup output,
container writable layers, configured logs and model caches; the marker must not be recoverable
through application/admin interfaces or persistent storage. Do not store these inspection results
as real user transcripts. Check configuration disables SQL parameter logging, proxy/API access
logs, tracing payloads, inference prompt logs, swap and core dumps. Client local/session storage,
IndexedDB and browser caches must contain no messages. Verify inference slot erase on success,
failure and cancellation. This tests no persistent copies/application restoration; it does not
claim forensic memory erasure.

## Real-model and release qualification

Use real eligible sources and human-authored nonpersonal evaluation questions. At least 60
questions, at least two answerable per selected topic, and at least 20 adverse/context/personal
cases; increase total if topic coverage requires it. Cases include expected claims, supporting
source IDs, applicability and expected refusal/clarification. Do not blindly score text equality.

```sh
docker compose --env-file deploy/.env -f deploy/compose.yaml -f deploy/compose.gpu.yaml up -d db inference api web
docker compose --env-file deploy/.env -f deploy/compose.yaml -f deploy/compose.gpu.yaml run --rm operator python -m app.cli evaluate --cases /work/evaluation.json --output /work/report.json
```

The operator container mounts only source manifests and qualification configuration and nonpersonal evaluation files.
The evaluation command creates aggregate results and flags cases for human scoring. The complete
release report must demonstrate SC-001–008, including zero unsupported evaluated official claims,
100% correct fail-safe/context/withdrawal checks, >=90% complete answerable answers, >=95% response
time under 10 seconds, the 10-student usability study, and retention inspection. Report performance
for correct answerable responses separately so quick refusals cannot hide an unusable model.

Load test the 50,000-block, 200-session, four-concurrent-generation planning envelope on the chosen
host. Record hardware, pinned revisions and results. If quality or latency fails, tune retrieval,
model or hardware and rerun affected gates; never silently relax success criteria. Review selected
accessibility checks manually and do not label the product fully conformant without a full review.

## Deployment and recovery rehearsal

Production Compose must run migrations before API readiness, use read-only application/model
containers, private internal ports, file-mounted secrets, no test data, and HTTPS. Build manifest
pins all images/models/dependencies. Back up source/governance data daily, with a separate durable
withdrawal journal containing no conversations. Rehearse restoring into an isolated environment:
replay later withdrawals, requalify sources, quarantine unresolved eligibility, then enable
readiness. Target corpus recovery point <=24 hours and restore <=4 hours. Never restore chats.
Restart the API and verify old sessions cannot be recovered and new sessions work; target service
readiness <=5 minutes with cached model files. No campus-wide uptime guarantee is inferred.

Planning finishes here. Generate implementation work with `$speckit-tasks` before executing this
guide against an application implementation.
