# Phase 7 RAG Offline-Pipeline Validation

Validated on October 4, 2026 with the production `ingest-pnw-demo` Docker Compose service and a
migrated PostgreSQL 17 database with pgvector.

The command below completed successfully:

```bash
docker compose --env-file deploy/.env -f deploy/compose.yaml \
  --profile operations run --rm ingest-pnw-demo
```

The follow-up database query joined `sources`, `source_versions`, `evidence_blocks`, and
`embeddings`. It produced the following bounded result:

| Public PNW source | Qualification state | Versions | Embeddings | Dimensions |
|---|---:|---:|---:|---:|
| `https://www.pnw.edu/dean-of-students/policies/academic-integrity-policy/` | eligible | 1 | 8 | 384 |
| `https://www.pnw.edu/getting-to-pnw/parking-and-fees/regulations-and-enforcement/` | eligible | 1 | 46 | 384 |
| `https://www.pnw.edu/registrar/academic-schedule/` | quarantined | 1 | 33 | 384 |

All three real documents passed fetch, parsing, semantic chunking, MiniLM embedding, and pgvector
storage. The Academic Schedule remained quarantined with `deadline_context_missing`; this is the
required safe behavior because the generic page does not attach complete term and session scope to
every extracted deadline chunk. Quarantined vectors remain unavailable as official answer evidence.

A second run reported `created_source_count: 0`, reused the three immutable source versions and
existing embeddings, and recorded fresh automated qualification results. This verifies idempotent
manifest import and content-version reuse.
