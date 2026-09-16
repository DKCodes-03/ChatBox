# Data Model: PNW Student Information Chatbot

## Overview

The system stores only the minimal operational data needed to answer student questions from approved university sources. Conversation history is kept in-memory or within a short-lived session object and is not persisted for v1.

## Core entities

### 1. SourceDocument

Represents an approved university page or official document that can answer student questions.

| Field | Type | Notes |
|---|---|---|
| id | UUID | Primary key |
| title | text | Official page or document title |
| url | text | Canonical source URL |
| source_type | enum | webpage, pdf, handbook, catalog, policy |
| campus_scope | text | Optional: Hammond, Westville, both |
| academic_term | text | Optional term or cycle represented by the source |
| last_updated | date | Best available update or publication date |
| status | enum | active, archived, disputed |
| reviewed_by | text | University owner or designated reviewer |
| content_hash | text | Hash to detect changed or replaced source content |
| created_at | timestamp | Audit record |

Validation rules:
- `url` must be non-empty and unique.
- `status` cannot be `active` for a source that is unavailable or clearly expired.
- A source used for official policy must include either a `last_updated` value or a clear review status.

### 2. SourceChunk

Represents a searchable unit extracted from a source document.

| Field | Type | Notes |
|---|---|---|
| id | UUID | Primary key |
| document_id | UUID | FK to `SourceDocument` |
| chunk_index | integer | Order within the document |
| heading | text | Section titles or page labels |
| content | text | Chunk text used for retrieval |
| page_ref | text | Page number or section reference |
| embedding | vector | pgvector embedding for semantic search |
| created_at | timestamp | |

Validation rules:
- `content` must be non-empty.
- `embedding` must be present for any chunk eligible for semantic retrieval.
- `chunk_index` must be >= 1.

### 3. ConversationSession

Tracks the current chat scope within a single browser session, but not long-term transcripts.

| Field | Type | Notes |
|---|---|---|
| id | UUID | Primary key |
| session_token | text | Browser-side session identifier |
| campus_context | text | Optional campus context |
| term_context | text | Optional term context |
| topic_scope | text | Current topic or subject area |
| created_at | timestamp | Session start |
| expires_at | timestamp | Short-lived expiry policy |

Validation rules:
- `session_token` must be unique.
- `expires_at` is required; sessions are ephemeral by design.
- `campus_context` and `term_context` are optional but should be retained within a single session when provided.

### 4. StudentQuestion

Stores the incoming question and the extracted scope context used for retrieval.

| Field | Type | Notes |
|---|---|---|
| id | UUID | Primary key |
| session_id | UUID | FK to `ConversationSession` |
| raw_text | text | Original student question |
| normalized_text | text | Lower-case text used for matching and logging |
| campus_hint | text | Optional campus from the question |
| term_hint | text | Optional academic term from the question |
| user_intent | text | High-level intent (deadline, requirement, procedure, etc.) |
| created_at | timestamp | |

Validation rules:
- `raw_text` must be present.
- The system may infer a `campus_hint` or `term_hint`, but it must ask a follow-up question when the answer would be ambiguous.

### 5. AnswerRecord

Stores the final answer returned to the student and any safety metadata.

| Field | Type | Notes |
|---|---|---|
| id | UUID | Primary key |
| session_id | UUID | FK to `ConversationSession` |
| question_id | UUID | FK to `StudentQuestion` |
| answer_text | text | Response shown to the student |
| answer_type | enum | supported, insufficient_information, conflict, escalation |
| confidence | float | Numeric confidence for monitoring |
| has_direct_link | boolean | Whether an official source link was included |
| escalation_target | text | Office or advisor if required |
| created_at | timestamp | |

Validation rules:
- `answer_text` must not be empty.
- If `answer_type = supported`, then at least one citation is required.
- If `answer_type != supported`, the response must include a safe-failure message and an escalation path when available.

### 6. Citation

Links an answer back to the evidence used to support it.

| Field | Type | Notes |
|---|---|---|
| id | UUID | Primary key |
| answer_id | UUID | FK to `AnswerRecord` |
| document_id | UUID | FK to `SourceDocument` |
| chunk_id | UUID | FK to `SourceChunk` |
| quote_snippet | text | Cited excerpt |
| link_url | text | Direct official URL |
| created_at | timestamp | |

Validation rules:
- `link_url` must be present for direct-source answers.
- `quote_snippet` must not exceed the source chunk length or be fabricated beyond the evidence text.

### 7. EscalationDestination

Contains approved office or advisor destinations for referrals.

| Field | Type | Notes |
|---|---|---|
| id | UUID | Primary key |
| name | text | Office or department name |
| category | text | academic, financial aid, registrar, dean of students |
| url | text | Official destination URL |
| email | text | Optional contact email |
| phone | text | Optional phone number |
| created_at | timestamp | |

Validation rules:
- `category` and `name` must be non-empty.
- A direct URL must be present for any referral the system recommends.

## Relationships

- `SourceDocument` 1:N `SourceChunk`
- `ConversationSession` 1:N `StudentQuestion`
- `StudentQuestion` 1:1 `AnswerRecord`
- `AnswerRecord` 1:N `Citation`
- `SourceDocument` 1:N `Citation`
- `EscalationDestination` may be referenced from `AnswerRecord` when the answer is not supported

## State transitions

- `SourceDocument`: active -> archived -> disputed
- `AnswerRecord`: supported -> insufficient_information -> escalation
- `ConversationSession`: active -> reset -> expired

## Design notes for policy safety

- The system will not persist chat transcripts for v1.
- Source trust is represented as document-level metadata, not as a blanket assumption that all public pages are equally authoritative.
- Any answer that does not have sufficient evidence will be marked as `insufficient_information` or `conflict` and will not be presented as a supported procedural answer.
