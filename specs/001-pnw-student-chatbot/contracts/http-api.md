# HTTP and UI Contract

## Transport and shared rules

Same-origin HTTPS `/api/v1`; JSON UTF-8. FastAPI generates OpenAPI from typed request/response
models; contract tests MUST compare generated schemas with this document. No public governance
HTTP endpoints. Body limits: 8 KiB request, message 1–4000 characters. Reject unknown fields.
Responses and chat routes use `Cache-Control: no-store`; proxy request/response bodies, cookies,
query strings and access logs are disabled. Validate Origin on mutating browser requests.
Session credentials are never included in JSON or URLs. User-provided URLs do not initiate fetches.

## Endpoints

| Method/path | Request | Success | Failure |
| --- | --- | --- | --- |
| POST /sessions | empty object | 201; set opaque session cookie; `{expires_at, server_time}` | 429 capacity exceeded, 503 unavailable |
| POST /messages | `{message: string, context?: Context}`; session cookie | 200 AnswerEnvelope; resets idle time on accepted message | 401 missing/unknown cookie; 410 expired; 409 concurrent request; 422 invalid; 429 limited; 503 unavailable |
| GET /session | session cookie | 200 `{expires_at, server_time}`; no transcript, no idle reset | 401 unknown; 410 expired |
| DELETE /session | session cookie, no body | 204 after invalidation and cancellation cleanup; clear cookie; idempotent for missing sessions | 503 cleanup unavailable; token remains invalid |
| GET /health/live | none | 200 process alive | 503 process cannot serve |
| GET /health/ready | none | 200 DB, eligible corpus and local inference ready | 503 sanitized reason code |

Readiness must not expose sources, credentials or infrastructure details. Expired token tombstones
may exist in memory briefly without content to distinguish 410 from 401; unknown tokens also
cause a fresh conversation. 401/410 never echo or replay the rejected message automatically.
The UI creates a new session and asks the student to resubmit with necessary context.

Context: optional `campus`, `term`, `year`, `session`, `program`, `student_level`, `catalog_year`.
Values are validated against eligible metadata, not accepted as proof of policy or eligibility.
Explicit user corrections replace old values and invalidate dependent answer assumptions.

## AnswerEnvelope

Required fields:

- `outcome`: `answer | partial | clarification | unable`.
- `segments`: ordered objects `{kind: explanation|step|limitation, text, citation_ids: string[]}`.
- `citations`: `{id, source_title, url, section?, page?, version_id}` objects. Every policy claim
  references at least one evidence-backed citation. URLs come from eligible source metadata.
- `context`: applicable Context; unknown values omitted, never silently guessed.
- `clarification`: null or `{question, fields: string[], options?: string[]}`.
- `referral`: null or `{office_name, contact_label, contact_url, citation_ids}`.
- `reason_code`: null or `missing_evidence | conflict | expired_source | context_required |
  personal_case | out_of_scope | source_unavailable | processing_unavailable`.
- `expires_at`, `server_time`: RFC 3339 instants.

An `answer` has supported explanation/steps. `partial` contains clearly separated supported
segments and limitations. `clarification` supplies a question and makes no claim whose context
is unknown. `unable` explains inability and may carry a verified referral; it must not cite
irrelevant material to appear supported. References to conflicting evidence are allowed in
limitations, but not used to assert the disputed conclusion. No raw HTML or untrusted Markdown.

Error envelope: `{error: {code, message, retryable}, server_time}` with fixed safe messages.
Never return submitted text in validation errors or exception payloads. 429 includes Retry-After.
A dependency timeout, Gemini quota response, invalid Gemini payload or provider data-policy
failure returns a safe limitation or 503, never a generated guess. The provider adapter may
select the configured local fallback only when that deployment option is enabled. No token
streaming: validate the complete response and current source eligibility before any answer is
exposed. Gemini API keys, provider request IDs and provider logs never appear in the response.

## Concurrency and limits

One outstanding generation per session. Initial limits: 6 accepted messages/minute/session,
200 active sessions and 4 simultaneous generations per deployment. Exceeding a limit returns
429 without creating persistent records; rate-limit state is volatile. Request wall time is
9 seconds, including queueing, retrieval, generation and validation. Timeouts cancel inference.
Requests may reset inactivity only when admitted as student messages, not on malformed input,
health polling or retry loops. Expiry/end invalidates all pending answers even if a dependency
returns later. Final source eligibility checks and emission are coordinated with source
withdrawal locks: an answer cannot be emitted after a prior committed withdrawal.

## Browser experience and accessibility

Single React chat view with initial scope/retention notice, labeled input, send, visible End chat,
loading status, structured answer, citations, clarification and error/retry states. At timeout,
clear messages and input drafts and show a session-ended notice. Never auto-retry a message
into a new session. Use server expiry and recheck on visibility/focus changes; activity means
sending a message, not typing or reading.

Keyboard operation, visible focus, programmatic labels, polite live announcements, readable
contrast and reflow at 320 CSS pixels are acceptance checks. Warn at 28 minutes of inactivity
and allow a student to send a visible “Continue this conversation” message; this is a student
message and resets the same 30-minute timer without background keepalives. Do not claim full
WCAG conformance based solely on automated checks; perform manual keyboard/screen-reader review.
