BACKEND_DIR := backend
FRONTEND_DIR := frontend

.PHONY: format format-check lint typecheck test test-db test-ingestion-integration check

format:
	python -m ruff format $(BACKEND_DIR)
	npm --prefix $(FRONTEND_DIR) run format

format-check:
	python -m ruff format --check $(BACKEND_DIR)
	npm --prefix $(FRONTEND_DIR) run format:check

lint:
	python -m ruff check $(BACKEND_DIR)
	npm --prefix $(FRONTEND_DIR) run lint

typecheck:
	python -m mypy $(BACKEND_DIR)
	npm --prefix $(FRONTEND_DIR) run typecheck

test:
	python -m pytest $(BACKEND_DIR)/tests
	npm --prefix $(FRONTEND_DIR) run test:run

test-db:
	docker compose up -d db
	docker compose exec -T db sh -c 'if ! psql -U "$$POSTGRES_USER" -d "$$POSTGRES_DB" -tAc "SELECT 1 FROM pg_database WHERE datname = '\''pnw_chatbot_test'\''" | grep -q 1; then createdb -U "$$POSTGRES_USER" pnw_chatbot_test; fi'
	docker compose exec -T db sh -c 'if ! psql -U "$$POSTGRES_USER" -d pnw_chatbot_test -tAc "SELECT to_regclass('\''public.corpus_builds'\'')" | grep -q corpus_builds; then psql -v ON_ERROR_STOP=1 -U "$$POSTGRES_USER" -d pnw_chatbot_test; fi' < $(BACKEND_DIR)/app/db/migrations/001_initial_schema.sql

test-ingestion-integration: test-db
	@set -a; [ ! -f .env ] || . ./.env; set +a; \
	cd $(BACKEND_DIR) && CHATBOX_TEST_DATABASE_URL="$${CHATBOX_TEST_DATABASE_URL:-postgresql+asyncpg://$${POSTGRES_USER:-pnw_chatbot}:$${POSTGRES_PASSWORD:-change-me-for-local-development}@localhost:$${POSTGRES_PORT:-5432}/pnw_chatbot_test}" \
		uv run pytest tests/integration/test_corpus_ingestion.py -q

check: format-check lint typecheck test