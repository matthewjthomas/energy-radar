.PHONY: test test-cov dev-up dev-down dev-logs install-dev

install-dev:
	python3 -m pip install -r requirements-dev.txt

test:
	pytest

test-cov:
	pytest --cov=app --cov-report=term-missing

dev-up:
	docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build -d

dev-down:
	docker compose -f docker-compose.yml -f docker-compose.dev.yml down

dev-logs:
	docker compose -f docker-compose.yml -f docker-compose.dev.yml logs -f app
