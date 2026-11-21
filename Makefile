.PHONY: setup up down test lint typecheck doctor build crawl

setup:                              ## نصب وابستگی‌ها با uv
	uv sync --group dev

up:                                 ## اجرای زیرساخت (Postgres + Redis)
	docker compose up -d

down:
	docker compose down

test:                               ## اجرای تست‌ها
	uv run pytest tests/ -v

lint:                               ## بررسی سبک کد
	uv run ruff check packages apps tests
	uv run ruff format --check packages apps tests

typecheck:                          ## بررسی تایپ‌ها
	uv run mypy packages apps

doctor:                             ## بررسی سلامت اتصال‌ها
	uv run python -m apps.cli.main doctor

crawl:                              ## کرال یک پیج: make crawl USER=username
	uv run python -m apps.cli.main crawl $(USER)

build:                              ## ساخت کاتالوگ: make build USER=username
	uv run python -m apps.cli.main build $(USER)
