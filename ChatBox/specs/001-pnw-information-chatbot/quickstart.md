# Quickstart: Local Validation

## Prerequisites

- Docker Desktop or Docker Engine
- Docker Compose v2
- Node.js 20+ for frontend tooling if running outside containers
- Python 3.12+ for backend tests if running outside containers
- At least 8 GB RAM recommended for running the local LLM container

## Start the stack

```bash
cd /mnt/c/Users/SJA/Desktop/School/PNW/CS52520/projects/ChatBox/ChatBox
docker compose up --build
```

This starts:
- Frontend: http://localhost:3000
- Backend API: http://localhost:8000
- PostgreSQL with pgvector: localhost:5432
- Ollama local LLM service: http://localhost:11434
- API docs: http://localhost:8000/docs

Pull the free model used by the application if the deployment does not
initialize it automatically:

```bash
docker compose exec ollama ollama pull llama3.2:3b
```

The application must not require a paid API key. If the local model is
unavailable, the backend must return a safe error or escalation response rather
than silently presenting an unsupported answer.

## Validate the key user flows

### 1. Health check

```bash
curl http://localhost:8000/api/health
```

Expected result:
- HTTP 200
- JSON response with `{"status":"ok"}`

### 2. Supported answer flow

```bash
curl -X POST http://localhost:8000/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"What is the deadline for spring registration?"}'
```

Expected result:
- Response includes a grounded answer text
- `answer_type` is `supported`
- At least one citation with a URL is returned

### 3. Missing context follow-up

```bash
curl -X POST http://localhost:8000/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"What is the deadline for registration?"}'
```

Expected result:
- Response includes a `follow_up_question` or a request for campus/term clarification
- No unsupported policy answer is returned

### 4. Safe-failure flow

```bash
curl -X POST http://localhost:8000/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"Can you tell me my exact final grade?"}'
```

Expected result:
- `answer_type` is `insufficient_information` or `escalation`
- Answer explains the limitation and points to the right office or advisor

## Optional local backend tests

```bash
cd /mnt/c/Users/SJA/Desktop/School/PNW/CS52520/projects/ChatBox/ChatBox/backend
pytest
```

## Optional local frontend checks

```bash
docker compose exec frontend npm test -- --run
```

## Stop the stack

```bash
docker compose down
```
