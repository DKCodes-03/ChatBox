# Chatbot API Contract

## Base URL

Local development: `http://localhost:8000`

All endpoints use JSON unless otherwise noted.

## `GET /api/health`

Returns the backend service health status.

### Response `200 OK`

```json
{
  "status": "ok"
}
```

## `POST /api/chat`

Accepts a natural-language student question and returns a grounded answer,
clarification request, safe limitation, or escalation.

### Request body

```json
{
  "message": "What is the deadline for spring registration?",
  "session_id": "optional-session-id",
  "campus_hint": "Hammond"
}
```

| Field | Required | Type | Description |
|---|---:|---|---|
| `message` | Yes | string | Student's natural-language question |
| `session_id` | No | string or null | Ephemeral conversation session identifier |
| `campus_hint` | No | string or null | Campus supplied by the student or prior context |

### Response `200 OK`

```json
{
  "answer": "Spring registration opens on ...",
  "answer_type": "supported",
  "citations": [
    {
      "title": "Official source title",
      "url": "https://www.pnw.edu/example",
      "snippet": "Relevant supporting passage."
    }
  ],
  "escalation_target": null,
  "follow_up_question": null
}
```

| Field | Required | Type | Description |
|---|---:|---|---|
| `answer` | Yes | string | Student-facing response |
| `answer_type` | Yes | enum | `supported`, `insufficient_information`, `conflict`, or `escalation` |
| `citations` | Yes | array | Supporting source citations; supported answers must include at least one |
| `escalation_target` | No | string or null | Official office, advisor, or destination for follow-up |
| `follow_up_question` | No | string or null | Focused question when campus, term, or other required context is missing |

### Citation object

| Field | Required | Type | Description |
|---|---:|---|---|
| `title` | Yes | string | Official source or document title |
| `url` | Yes | string | Direct official source link |
| `snippet` | Yes | string | Supporting source passage |

### Response behavior

- `supported`: The answer is grounded in retrieved source evidence and includes
  direct citations.
- `insufficient_information`: The approved source corpus does not support a
  reliable answer. The response must not invent details.
- `conflict`: Relevant sources materially disagree or their freshness cannot be
  established. The response must identify the uncertainty.
- `escalation`: The request requires a personal record, binding decision, or
  authorized staff action.
- If required scope is missing, the API returns a focused
  `follow_up_question` before providing a scope-specific answer.

### Error responses

- `400 Bad Request`: Missing or ambiguous required context that cannot be
  represented as a normal follow-up response.
- `422 Unprocessable Entity`: Request validation failure.

## `POST /api/chat/reset`

Clears the current ephemeral conversation context. It must not create or retain
a transcript.

### Request body

```json
{
  "session_id": "session-id"
}
```

| Field | Required | Type | Description |
|---|---:|---|---|
| `session_id` | Yes | string | Ephemeral session to reset |

## `GET /api/sources/search`

Searches indexed public PNW source documents by text and optional scope.

### Query parameters

| Parameter | Required | Type | Description |
|---|---:|---|---|
| `q` | Yes | string | Search text |
| `campus` | No | string | Campus filter |
| `term` | No | string | Academic term filter |

### Response `200 OK`

```json
{
  "results": [
    {
      "id": "source-id",
      "title": "Official source title",
      "url": "https://www.pnw.edu/example",
      "status": "active"
    }
  ]
}
```

### Source document summary

| Field | Required | Type | Description |
|---|---:|---|---|
| `id` | Yes | string | Source document identifier |
| `title` | Yes | string | Official page or document title |
| `url` | Yes | string | Canonical official URL |
| `status` | Yes | enum | `active`, `archived`, or `disputed` |

## Contract constraints

- Chat sessions are ephemeral in version 1.
- Chat transcripts must not be persisted.
- Supported answers must preserve relevant campus, term, date, prerequisite, and
  condition details.
- Answers must not make binding decisions about a student's personal record.
- The API must surface uncertainty rather than silently choosing between
  conflicting or stale sources.
