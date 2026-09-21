# ChatBox

ChatBox is a student information chatbot for Purdue University Northwest. The
project combines a React/Vite frontend, a FastAPI backend, PostgreSQL with
pgvector, and a local Ollama model.

## Prerequisites

- Docker Desktop with WSL integration enabled, or Docker Engine with Docker
	Compose v2
- GNU Make
- At least 8 GB of RAM recommended for the local LLM container

For local, non-container development also install Node.js 20+ and Python 3.12.

## Start the development stack

Run these commands from the `ChatBox/` directory:

```bash
cp .env.example .env
docker compose up --build
```

The services are available at:

- Frontend: http://localhost:3000
- Backend API: http://localhost:8000
- Backend API docs: http://localhost:8000/docs
- Ollama: http://localhost:11434
- PostgreSQL: `localhost:5432`

The first startup may take several minutes while Docker builds the images and
downloads the database and Ollama images.

## Initialize the local model

In another terminal, from `ChatBox/`, download the development model into the
persistent Ollama volume:

```bash
docker compose exec ollama ollama pull llama3.2:3b
```

The model is kept between container restarts. No paid API key is required.

## Run implementation checks

Install GNU Make if it is not already available, then run the project quality
gate from `ChatBox/`:

```bash
source backend/.venv/bin/activate
make check
```

The individual commands are:

```bash
make format-check
make lint
make typecheck
make test
```

To create the backend environment for local checks:

```bash
python3.12 -m venv backend/.venv
source backend/.venv/bin/activate
python -m pip install -r backend/requirements.txt
```

The current setup tests the project shell and frontend application. API health,
chat, retrieval, and corpus commands will become available as their later
implementation tasks are completed.

## Stop the stack

```bash
docker compose down
```

To also remove local PostgreSQL and Ollama data:

```bash
docker compose down -v
```

The `-v` option is destructive for local database and model data.
