BACKEND_DIR := backend
FRONTEND_DIR := frontend

.PHONY: format format-check lint typecheck test check

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

check: format-check lint typecheck test