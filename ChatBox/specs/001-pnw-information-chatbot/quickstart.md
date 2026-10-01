# Quickstart: Local Validation

Use this guide from the project directory that contains the Compose file, the `backend/` and `frontend/` folders, and the `specs/` directory. If your repo is checked out with a nested `ChatBox` folder, run `cd ChatBox` first.

## Prerequisites

- Docker Desktop or Docker Engine
- Docker Compose v2
- GNU Make for the shared project commands
- Node.js 20+ for frontend tooling if you want to run it outside containers
- Python 3.12+ for backend work outside containers
- `uv` is recommended for backend-only local commands, though the project also supports a venv created with `python3.12 -m venv`
- At least 8 GB RAM recommended for the local LLM container

From WSL, Docker Desktop must have WSL integration enabled for the active distribution. Verify the connection with:

```bash
docker version
docker compose version
```

## 1. Start the stack

From the project directory:

```bash
cp .env.example .env
docker compose up --build
```

This starts:
- Frontend: http://localhost:3000
- Backend API: http://localhost:8000
- PostgreSQL with pgvector: localhost:5432
- Ollama local LLM service: http://localhost:11434
- API docs: http://localhost:8000/docs

The first startup may take several minutes while Docker builds the images and downloads PostgreSQL and Ollama.

## 2. Initialize the required local models

Pull the generation model used by the app if it is not already present:

```bash
docker compose exec ollama ollama pull llama3.2:3b
```

Pull the embedding model used for the corpus and retrieval pipeline:

```bash
docker compose exec ollama ollama pull nomic-embed-text
```

The models are stored in the `ollama_data` volume and remain available after future container restarts. No paid API key is required.

## 3. Prepare the RAG vector database

Add or update source entries in `backend/ingestion/sources.yaml`. A source is ingested only when `review_status` is `active`, `reviewer` is set, and `last_reviewed` is within its configured review cadence. Only explicit URLs in the manifest are fetched; `follow_links` does not currently crawl linked pages. The manifest host allowlist also applies to redirects.

Validate the manifest without writing to the database or calling Ollama:

```bash
cd backend
uv run python -m ingestion.validate_sources
```

Each source reports fetch, extraction, and chunking results. The command exits with status 1 if any source cannot be fetched or parsed. `--manifest PATH` selects another YAML manifest.

For a fresh database, initialize the schema once and then build the corpus:

```bash
docker compose up -d db ollama
docker compose exec -T db psql -U pnw_chatbot -d pnw_chatbot < backend/app/db/migrations/001_initial_schema.sql
docker compose run --rm backend python -m ingestion.build_corpus
```

The schema migration is required once for a fresh database. The command skips sources still pending review, fetches the remaining approved webpages and PDFs, extracts and chunks their text, embeds each chunk with Ollama, and stores documents and vectors in PostgreSQL/pgvector. It promotes the new corpus only after every source succeeds; on failure, the candidate is marked failed and the previous promoted corpus remains active. PDF files without extractable text, including scanned PDFs, must be OCR-processed separately before ingestion.

If every approved source hash and its manifest metadata already match the promoted corpus, the command prints `Data is up-to-date; to add new data, update sources.yaml.` and does not create another corpus build or embeddings.

For a backend-only local run, use:

```bash
cd backend
uv run python -m ingestion.build_corpus
```

Use `--manifest PATH` to select a different YAML manifest.

The application must not require a paid API key. If the local model is unavailable, the backend must return a safe error or escalation response rather than silently presenting an unsupported answer.

## 4. Validate the key user flows

### Health check

```bash
curl http://localhost:8000/api/health
```

Expected result:
- HTTP 200
- JSON response with `{"status":"ok"}`

### Supported answer flow

```bash
curl -X POST http://localhost:8000/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"What is the deadline for spring registration?"}'
```

Expected result:
- Response includes a grounded answer text
- `answer_type` is `supported`
- At least one citation with a URL is returned

### Missing context follow-up

```bash
curl -X POST http://localhost:8000/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"What is the deadline for registration?"}'
```

Expected result:
- Response includes a `follow_up_question` or a request for campus/term clarification
- No unsupported policy answer is returned

### Safe-failure flow

```bash
curl -X POST http://localhost:8000/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"Can you tell me my exact final grade?"}'
```

Expected result:
- `answer_type` is `insufficient_information` or `escalation`
- Answer explains the limitation and points to the right office or advisor

## 5. Run the repository checks

Run the shared quality gate from the project root:

```bash
python3.12 -m venv backend/.venv
source backend/.venv/bin/activate
python -m pip install -r backend/requirements.txt
make check
```

Or, if you prefer the project make commands without a custom venv:

```bash
make format-check
make lint
make typecheck
make test
```

## 6. Integration test for corpus ingestion

This integration test exercises fetching, parsing, chunking, embedding, and PostgreSQL vector persistence. The project Makefile starts PostgreSQL, creates and migrates the dedicated `pnw_chatbot_test` database without touching the development database, then sets `CHATBOX_TEST_DATABASE_URL` and runs the test:

```bash
make test-ingestion-integration
```

The test database URL must end with `_test`. Set `CHATBOX_TEST_DATABASE_URL` in `.env` if your local database credentials or port differ from the defaults.

## 7. Optional local backend and frontend checks

Backend-only run from `backend/`:

```bash
cd backend
python -m pytest
```

Frontend-only run from `frontend/`:

```bash
cd frontend
npm install
npm run test:run
npm run lint
npm run typecheck
```

## 8. Stop the stack

```bash
docker compose down
```

To also remove local PostgreSQL and Ollama data:

```bash
docker compose down -v
```

The `-v` option is destructive for local database and model data.
