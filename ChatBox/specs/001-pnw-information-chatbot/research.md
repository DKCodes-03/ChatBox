# Research: PNW Student Information Chatbot

## Decision

Use a Dockerized, browser-based web application with a FastAPI backend, a React frontend, PostgreSQL with pgvector for retrieval and grounding, and Ollama running a free open-weight model such as `llama3.2:3b` for answer drafting.

## Rationale

- The feature is a student-facing Q&A system over university information, which fits a web application split between a lightweight UI and a retrieval-augmented backend.
- The spec requires grounded answers, source attribution, safe fallback behavior, and explicit campus/term handling. A server-side retrieval service is the safest place to enforce those rules.
- PostgreSQL with pgvector is a good fit for storing source passages and performing semantic retrieval without introducing a separate vector database.
- Docker Compose aligns with the requirement for deployment simplicity and reproducible environment setup.
- The spec explicitly says conversations remain ephemeral for v1, which favors a browser-side or in-memory conversation state with no transcript persistence.
- Ollama provides a local model-serving API without per-request charges or a hosted LLM key, which matches the free-version requirement and keeps student questions within the deployment environment.
- A small local model is sufficient for intent classification and evidence-constrained drafting when retrieval and policy validation remain authoritative.

## Alternatives considered

1. Single-service Python app with embedded frontend
   - Rejected because the frontend and backend need different deployment, testing, and scaling concerns, and the project requirement explicitly calls for React.

2. Node/Express backend with a relational database and separate vector store
   - Rejected because the requirement and project preference call for FastAPI and PostgreSQL with pgvector, which reduces operational complexity.

3. Full transcript retention with analytics storage
   - Rejected because the spec requires no transcript retention in v1 and the privacy risks outweigh the value for early launch.

4. External hosted LLM-only answer generation without retrieval memory
   - Rejected because the constitution requires approved-source grounding, explicit citations, and safe failures when evidence is insufficient.

5. Paid hosted LLM API
   - Rejected for the initial release because the requested free version should not depend on usage billing, API keys, or an external provider's availability.

## Design decisions tied to requirements

- Source grounding: the backend will keep approved university pages and linked official documents in a curated source corpus, with chunk-level metadata for title, URL, campus, term, and last-updated information.
- Follow-up context: the frontend will keep conversation context in a session object for the active chat only, while the backend will support a reset endpoint to clear scope-specific state.
- Safe failure: unsupported questions, conflicting sources, or missing context will produce an explicit uncertainty answer and a referral path rather than a fabricated policy answer.
- Deployment: Docker Compose will run backend, frontend, and PostgreSQL services together for local development and a consistent deployment target.
- LLM boundary: Ollama receives only the student question, relevant session scope, and retrieved source passages. The system must reject or replace any draft that cannot be supported by the retrieved evidence.
- Model governance: the implementation must verify the selected model's license and redistribution terms before deployment; "free" means no paid API usage, not an exemption from license obligations.
