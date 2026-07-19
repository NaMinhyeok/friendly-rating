.DEFAULT_GOAL := help

UV ?= uv
ENV_FILE ?= .env
PYTEST_ARGS ?=
RUNSERVER_ARGS ?=

.PHONY: help setup lint test check run

help:
	@printf '%s\n' \
		'make setup                         Install locked dependencies' \
		'make lint                          Run Ruff checks' \
		'make test [PYTEST_ARGS="..."]      Run isolated pytest' \
		'make check                         Run the full repository gate' \
		'make run [RUNSERVER_ARGS="..."]    Start the local Django server'

setup:
	$(UV) sync --locked

lint:
	./scripts/check lint

test:
	./scripts/check test -- $(PYTEST_ARGS)

check:
	./scripts/check

run:
	$(UV) run --env-file "$(ENV_FILE)" python manage.py runserver $(RUNSERVER_ARGS)
