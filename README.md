# PNW Student Information Chatbot

This project uses FastAPI, React, PostgreSQL with pgvector, a local MiniLM embedding model, and
Docker Compose. University claims are served only from independently qualified public evidence.

## Prepare the three-document PNW demo corpus

The checked-in manifest at `corpus/pnw-demo-sources.json` contains three real, public PNW pages:

1. Parking Regulations and Enforcement
2. Academic Schedule
3. Academic Integrity Policy

The demo job limits each fetch to its manifest URL. It parses each page, creates semantic chunks,
generates normalized 384-dimensional MiniLM embeddings, stores the chunks and vectors in
PostgreSQL/pgvector, and runs the same automated qualification rules used by the chatbot.

From the repository root, build the backend image:

```bash
docker compose --env-file deploy/.env -f deploy/compose.yaml build api
```

Download the free embedding model once. The model files go into the ignored
`deploy/models/embeddings` directory and are reused on later runs:

```bash
mkdir -p deploy/models/embeddings
docker run --rm --user 65532:65532 \
  -e HOME=/tmp \
  -v "$PWD/deploy/models/embeddings:/models/embeddings" \
  --entrypoint /app/.venv/bin/python \
  pnw-student-chatbot-api:local \
  -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2', cache_folder='/models/embeddings', trust_remote_code=False)"
```

Run the complete offline corpus pipeline:

```bash
docker compose --env-file deploy/.env -f deploy/compose.yaml \
  --profile operations run --rm ingest-pnw-demo
```

The command prints one JSON result. A successful run reports `"status":"success"`,
`"source_count":3`, and a positive `embedding_count` for every source. A source may be marked
`quarantined` when the page lacks enough source-backed applicability information; its vectors are
stored for diagnostics but retrieval will not use them as official evidence.

The checked result from the project workspace is recorded in
[`docs/phase7-rag-validation.md`](docs/phase7-rag-validation.md).
