# Data Model: PNW Student Information Chatbot

## Storage boundary

PostgreSQL/pgvector stores official university content, automated qualification results,
source lifecycle events and aggregate metrics only. No student messages, answers, query
embeddings, session tokens or IP addresses enter persistent tables, logs, WAL or backups.
Public corpus versions and nonpersonal evaluation fixtures can persist.

IDs are UUIDs; timestamps are timezone-aware UTC instants serialized as RFC 3339. Qualification
expiry is last successful qualification plus 24 hours, shortened by any known source effective
end. Unknown term/applicability cannot be inferred from fetch time.

## Persistent entities

| Entity | Fields and constraints | Relationships |
| --- | --- | --- |
| Source | id, unique canonical_url, title, media_type, candidate/eligible/quarantined/stale/withdrawn status | Has immutable versions; no approver field |
| SourceVersion | id, source_id, content_sha256, public content, fetched_at, effective_from/to, extraction_status, parser_version | Unique source/hash; immutable evidence |
| SourceLink | from_version_id, target_source_id, relation | Child independently qualifies |
| Qualification | id, version_id, rule_version, checked_at, valid_until, status, check_results, provenance_evidence, applicability_evidence | Append-only automated result; all required checks must pass |
| Applicability | version_id, topic, institution, campus, student_level, program, catalog_year, term, session, evidence_block_ids | Unknown is not universal; deadlines require term/session support |
| EvidenceBlock | id, version_id, ordinal, heading_path, page/anchor, text, structured_content, topic_key, scope | Complete semantic units and table headings/footnotes |
| Embedding | block_id, model_revision, dimensions, vector(384) | Unique block/revision; normalized corpus vectors only |
| CourseRelation | id, evidence_block_id, course_code, related_course_code, relation, group_expression, catalog_year, campus | Preserve AND/OR, grades and corequisites |
| OfficeReferral | id, office_name, responsibilities, contact_label, contact_url, evidence_block_id, scope | Eligibility derives from source evidence, not office approval |
| Conflict | id, topic_key, scope, status, detected_at, resolved_at, supersession_evidence_ids | Joins disputed blocks; unresolved conflicts block claims |
| SourceEvent | id, source_id, event_type, timestamp, rule_version, reason_code, evidence_ids | Qualification, change, withdrawal, restoration; no human sign-off required |
| IngestionRun | id, source_id, started_at, completed_at, status, bounded_error_code, version_id | Public-source jobs only |
| AggregateMetric | hour_bucket, bounded_metric_name, outcome_enum, count, sum_ms, histogram_buckets | No identifiers, free text or message content |

## Lifecycle and validation

1. Import the supplied corpus and bounded public links within configured PNW host/path rules
   as candidates. Matching a host does not make a document eligible.
2. Fetch, extract and check provenance, completeness, applicability and currency automatically.
   Incomplete tables, inaccessible PDFs and ambiguous scope are quarantined with reason codes.
3. Passing all checks makes the version eligible without human review. A failed check blocks
   affected evidence. Qualification stores the exact rule version and supporting metadata.
4. Recheck daily. Query-time freshness requires now < valid_until; scheduling delays cannot
   extend validity. A failed refresh blocks use immediately. Successful requalification can
   restore eligibility without office approval or manual renewal.
5. Material changes suspend old evidence until the replacement independently qualifies.
   Identical canonical content can receive a fresh qualification after all checks pass again.
6. Conflicts remain blocked unless explicit source-backed supersession resolves their overlapping
   scope. Fetch time and similarity rank cannot resolve contradictions. Human intervention is
   optional, not required to return a safe limitation.
7. Withdrawals and eligibility updates commit transactionally and coordinate with final answer
   authorization locks. Requalification cannot override an explicit withdrawal until an operator
   removes it; this optional administrative safeguard is not an approval requirement.

Runtime DB credentials read eligible corpus state and write bounded aggregate metrics only.
Ingestion/operator roles can update qualifications and lifecycle state; only migration/backup
roles manage schema/backups. Restore must replay withdrawals and requalify sources before use.

## Volatile session model

Session: random 256-bit token, created_at, last_student_message_at, expires_at, generation,
messages, explicit context, pending_request handle and per-session lock. Token is an opaque
Secure/HttpOnly/SameSite cookie; it never appears in URLs or persistent logs. One API process
owns the sessions. Worker replication is prohibited for this design.

- Creating a session starts a 30-minute initial idle clock. Each accepted student message sets
  expiry to its receipt time plus 30 minutes. Polling, bot replies and background work do not.
- Before accepting a message, test expiry while holding the session lock. At the exact expiry
  instant, the old session is invalid and cannot be revived by a simultaneous message.
- End chat/expiry invalidates the generation first, cancels processing, discards results, clears
  state and inference buffers, and removes the token. No request can commit a late answer.
- API restarts lose all sessions; the UI requests a new session and explains the reset.
- Browser state uses memory only: no localStorage, IndexedDB, sessionStorage, service-worker
  response cache, transcript export or analytics recording. Clear on expiry, end and pagehide;
  revalidate before displaying content after resume/back navigation. A sleeping browser cannot
  run timers, so it must clear expired content before rendering when execution resumes.
- No persistence-based session recovery. Deletion means unavailable for application restoration
  or staff review; secure forensic erasure of device RAM is not claimed.
