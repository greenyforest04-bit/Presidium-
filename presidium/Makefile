.PHONY: install test lint run docker clean

# ── Install dependencies ──
install:
	poetry install

# ── Run tests ──
test:
	poetry run pytest tests/ -v --tb=short

test-cov:
	poetry run pytest tests/ -v --cov=presidium --cov-report=term-missing

# ── Lint ──
lint:
	poetry run ruff check presidium/ tests/
	poetry run mypy presidium/ --ignore-missing-imports

format:
	poetry run ruff format presidium/ tests/

# ── Run the app ──
run:
	poetry run presidium

run-port:
	PRESIDIUM_NET_LISTEN_PORT=$(PORT) poetry run presidium

# ── Docker ──
docker:
	docker build -t presidium:latest .

docker-up:
	docker compose up -d

docker-down:
	docker compose down

docker-logs:
	docker compose logs -f

# ── Clean ──
clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	rm -rf .pytest_cache .mypy_cache .ruff_cache
	rm -rf dist/ *.egg-info
