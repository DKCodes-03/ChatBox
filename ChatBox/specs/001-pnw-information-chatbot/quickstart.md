# Quickstart: Local Validation

## Prerequisites

- Docker Desktop or Docker Engine
- Docker Compose v2
- GNU Make for the shared project commands
- Node.js 20+ for frontend tooling if running outside containers
- Python 3.12+ for backend tests if running outside containers
- At least 8 GB RAM recommended for running the local LLM container

From WSL, Docker Desktop must have WSL integration enabled for the active
distribution. Verify the connection with:

```bash
docker version
docker compose version
```

## Start the stack

```bash
cd /mnt/c/Users/SJA/Desktop/School/PNW/CS52520/projects/ChatBox/ChatBox
cp .env.example .env
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

The model is stored in the `ollama_data` volume and is available after future
container restarts. No paid API key is required.

## Prepare the RAG vector database

The ingestion commands below are planned application entrypoints. They become
available after the ingestion implementation tasks are completed.

Before testing supported answers, build and validate the initial corpus. The
ingestion job reads the curated source list, fetches official PNW webpages and
linked documents, extracts structure-preserving chunks, generates local
embeddings, and promotes the build only after retrieval checks pass.

```bash
docker compose run --rm backend python -m ingestion.embed_and_index
docker compose run --rm backend python -m ingestion.validate_corpus
```

Expected result:
- Every active source has a content fingerprint and at least one validated
  chunk.
- All indexed vectors use the configured embedding model and dimensions.
- A corpus version is marked `promoted` only after representative retrieval
  and citation checks pass.

If a source is unavailable, malformed, stale, or conflicting, the build keeps
it out of the active corpus and reports it for review. Re-running the build is
safe: unchanged sources are skipped, changed sources receive new chunks, and
the previous promoted corpus remains available until validation succeeds.

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

## Implementation checks

Run the shared quality gate from the `ChatBox/` directory. The backend virtual
environment must contain the development dependencies:

```bash
python3.12 -m venv backend/.venv
source backend/.venv/bin/activate
python -m pip install -r backend/requirements.txt
make check
```

Run individual checks when iterating:

```bash
make format-check
make lint
make typecheck
make test
```

## Optional local backend tests

```bash
cd /mnt/c/Users/SJA/Desktop/School/PNW/CS52520/projects/ChatBox/ChatBox/backend
pytest
```

## Optional local frontend checks

```bash
cd /mnt/c/Users/SJA/Desktop/School/PNW/CS52520/projects/ChatBox/ChatBox/frontend
npm install
npm run test:run
npm run lint
npm run typecheck
```

## Stop the stack

```bash
docker compose down
```
