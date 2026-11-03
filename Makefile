.PHONY: all check init clean format lint type test ui-test ui

RUN := uv run --locked

all: check

check: lint type test ui-test

init:
	uv sync --locked
	$(RUN) pre-commit install --install-hooks

clean:
	$(RUN) python -c "import shutil; from pathlib import Path; [shutil.rmtree(p, ignore_errors=True) for p in ('.mypy_cache', '.pytest_cache', '.ruff_cache', 'htmlcov')]; Path('.coverage').unlink(missing_ok=True)"

format:
	$(RUN) ruff format .
	$(RUN) ruff check --fix .

lint:
	$(RUN) ruff format --check .
	$(RUN) ruff check .

type:
	$(RUN) mypy

test:
	$(RUN) python -m pytest

ui-test:
	node --check ui/app.js
	node --test ui/tests/model.test.js

ui:
	$(RUN) python configure.py
