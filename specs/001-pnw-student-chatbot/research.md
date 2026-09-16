# Research Decisions

Research date: 2026-09-16. User-selected stack: FastAPI, React, PostgreSQL/pgvector, Docker.
Decisions below resolve design unknowns; performance and answer quality are release gates,
not results claimed for software that has not been built.

## 1. Application structure

**Decision:** Python 3.12, FastAPI/Pydantic 2, SQLAlchemy 2 with psycopg 3 and Alembic;
React 19 with TypeScript and Vite, built with Node 22. PostgreSQL 17 with pgvector 0.8 is
the compatibility baseline. Pin exact tested package versions, container digests and model
revisions when implementation creates lockfiles; these are intended baselines, not latest-version
claims. Use one API worker and an independent ingestion command.

**Rationale:** The product needs a small chat interface with separate API ownership. Session
state in process memory cannot transparently span FastAPI workers. Source ingestion must not
block the API event loop. These are design choices informed by the framework documentation.

**Alternatives considered:** A React full-stack framework adds server responsibilities already
owned by FastAPI. Multiple API workers require a separately verified ephemeral session service.

Sources: [React from scratch](https://react.dev/learn/build-a-react-app-from-scratch),
[FastAPI deployment concepts](https://fastapi.tiangolo.com/deployment/concepts/).

## 2. Retrieval and source authority

**Decision:** Combine PostgreSQL full-text search with exact pgvector cosine ranking over
SQL-filtered eligible evidence. Use reciprocal rank fusion for candidate ordering; retrieve
up to 20 candidates per ranking and select at most 8 complete evidence units for generation.
Course identifiers also use exact structured lookup. Rank thresholds are calibrated against
the eligible evaluation set and recorded, never described as policy confidence probabilities.

**Rationale:** pgvector exact search avoids approximate-index recall loss. Source qualification,
expiry, applicable scope, term applicability and conflicts are hard eligibility conditions. Check
missing context before selecting a specific campus/term; unknown scope is not universal.

**Alternatives considered:** HNSW is deferred until scale benchmarks justify it and filtered
recall passes the same evaluation. Vector similarity alone cannot establish authority or
resolve contradictory policy claims.

Source: [pgvector](https://github.com/pgvector/pgvector).

## 3. Concurrent withdrawal and expiry

**Decision:** Generate outside database transactions, then recheck every cited evidence version,
expiry and conflict under a short transaction coordinated with governance updates. Hold source
row locks through bounded response enqueue; a withdrawal must acquire conflicting locks before
committing. The enqueue is the answer authorization point, not the time a remote browser reads it.
If eligibility changed, discard the answer and return a safe limitation. Recheck time immediately
at authorization. No answer cache bypasses this gate.

**Rationale:** A retrieval-time database snapshot cannot detect later withdrawal. This locking
protocol is a design inference that needs an integration test with concurrent withdrawal.

**Alternatives considered:** Long transactions throughout generation unnecessarily block source
updates. A final unlocked query leaves a race between checking and publishing.

Sources: [PostgreSQL isolation](https://www.postgresql.org/docs/current/transaction-iso.html),
[date/time types](https://www.postgresql.org/docs/current/datatype-datetime.html).

## 4. Local generation and embeddings

**Decision:** Start with llama.cpp and Qwen/Qwen3-4B-GGUF Q4_K_M in non-thinking mode, bounded
output of 512 tokens, and local Sentence Transformers all-MiniLM-L6-v2 embeddings. Store
normalized 384-dimensional corpus vectors. Embedding retrieval units stay within 220 model
wordpieces including headings; retain complete parent passages, table rows and footnotes for
answer evidence. Oversized tables are split by semantic rows, never by arbitrary characters.

**Rationale:** Local inference permits direct control of prompt retention. MiniLM's documented
256-wordpiece input limit requires explicit segmentation. The small generator is an evaluation
baseline; fluent output is not proof of grounding. Validate claims and citations, abstain on
unsupported content, and use human-reviewed cases to measure residual errors.

**Alternatives considered:** Hosted inference requires a separately approved retention contract.
Larger local models are a fallback if quality gates fail; model replacement requires rerunning
quality, resource, deletion and latency checks. No vendor-specific cloud dependency is required.

Sources: [Qwen model card](https://huggingface.co/Qwen/Qwen3-4B-GGUF),
[MiniLM model card](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2),
[Sentence Transformers encoding](https://www.sbert.net/docs/package_reference/sentence_transformer/model.html),
[llama.cpp Docker](https://github.com/ggml-org/llama.cpp/blob/master/docs/docker.md).

## 5. Conversation lifecycle and inference cleanup

**Decision:** Active sessions exist in API/browser memory only. Student text and derived query
vectors never enter persistent stores. Disable prompt caching, idle-slot caching, checkpoint
caches, slot persistence and body logs in inference. The initial llama.cpp profile uses
`--no-cache-prompt --cache-ram 0 --ctx-checkpoints 0 --no-cache-idle-slots --log-disable`;
verify these flags against the pinned build. Assign exclusive inference slots and invoke
`POST /slots/{id}?action=erase` after every completion, failure or cancellation. A slot whose
cleanup cannot be confirmed is quarantined; restart inference before reuse. Access to slot
management is private. Erase query embedding references and disable embedding input caches.

**Rationale:** No durable transcript is necessary for follow-ups within one session. Expiry/end
invalidates the session generation first, cancels work and prevents late results from restoring
content. Run expiry handling independently of request traffic; check expiry on every access.
Model cache erasure is logical deletion, not a promise of forensic RAM zeroization.

**Alternatives considered:** PostgreSQL conversation rows would expose content through WAL and
backups even after deletion. Per-request model processes simplify isolation but impose repeated
model-load latency. Ordinary persistent tracing is incompatible with the approved policy.

Sources: [llama.cpp server](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md),
[Docker tmpfs and swap caveat](https://docs.docker.com/engine/storage/tmpfs/).

## 6. Ingestion and source qualification

**Decision:** Constitution v2.0.0 supersedes per-document human approval. Keep immutable source
versions and automated qualification records. Candidate discovery is bounded to the supplied
corpus and public links on configured PNW hosts. Every document passes provenance, extraction
completeness, scope and currency checks before use. Record rule versions and evidence. Run daily
checks; qualification expires 24 hours after the last successful check, or earlier when source
applicability ends. Failed refreshes block affected evidence immediately.

**Rationale:** This implements the user's removal of document-review gates while retaining
source-grounded answers. Fetch success cannot establish currency; explicit catalog/term/effective
metadata must support claims. Conflicting sources remain blocked unless source-backed supersession
resolves them. A safe limitation requires no human decision.

**Alternatives considered:** Per-document office sign-off is superseded. Trusting every hosted
page or automatically treating a recent fetch as current violates grounding requirements.
Beautiful Soup and pypdf remain the initial parsers with structural validation; inaccessible
or incomplete documents are quarantined, not routed into a mandatory manual approval queue.

## 7. Docker and operational envelope

**Decision:** Docker Compose services: web/proxy, API, PostgreSQL, local inference, scheduled
one-shot ingest, and one-shot migrations. Models are read-only files; source/database volumes
are persistent; chat scratch is memory-only. Disable host swap, core dumps and body/access logs
on chat paths. Use per-service file-mounted secrets and separate database roles. Only the web
port is public; internal database, inference and operator interfaces are inaccessible externally.
Use health checks and migration completion as startup dependencies, plus runtime timeouts.

**Rationale:** Compose fits a single-host initial deployment. Startup readiness does not replace
runtime failure handling. Host swap would undermine a memory-only retention design.

**Alternatives considered:** Kubernetes and multi-host sessions add operational complexity without
an established scale requirement. Container filesystem persistence is unsuitable for transcripts.

Sources: [FastAPI Docker](https://fastapi.tiangolo.com/deployment/docker/),
[Compose startup order](https://docs.docker.com/compose/how-tos/startup-order/),
[Compose production](https://docs.docker.com/compose/how-tos/production/),
[Compose secrets](https://docs.docker.com/compose/how-tos/use-secrets/).

## 8. Planning defaults for accessibility, scale and recovery

**Decision:** Validate keyboard operation, labels, focus, screen-reader status announcements,
contrast and small-screen reflow. Show an inactivity warning at 28 minutes and permit an explicit
student continuation message, preserving the eligible 30-minute timer. Do not claim complete
WCAG conformance from these selected tests. Use 200 active sessions, four concurrent generations
and 50,000 evidence units as initial engineering test bounds; benchmark on the deployment host.
A 24 GiB GPU and 32 GiB host RAM are a starting benchmark configuration, not a capacity guarantee.
CPU-only development is supported with no latency claim. API restart intentionally ends sessions.
Target restart readiness within five minutes with model weights cached; daily corpus backups
have a 24-hour recovery point target and a four-hour restore drill target.

**Rationale:** These are adjustable planning defaults for previously deferred operational details,
not changes to answer-safety or retention requirements. The fixed idle limit may require further
university accessibility review before any formal conformance claim. Inference quality and
latency must both pass; fast refusals cannot substitute for answerable-case success.

**Alternatives considered:** An unsupported campus-wide load or availability guarantee would be
misleading without traffic and hosting evidence. Persistent session recovery is explicitly excluded.

Source: [W3C WCAG 2.2](https://www.w3.org/TR/WCAG22/).

## Resolution status

No unresolved technical clarification remains. Passing source-qualification checks, production secrets,
model artifacts, host sizing evidence and acceptance results are deployment inputs to obtain
and validate during implementation, not fabricated facts or completed checks.
