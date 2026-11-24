# Project Stingray developer task shortcuts
# Usage: make <target>

.PHONY: help setup lint format-check typecheck test test-hardware run-mock preflight clean

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-14s %s\n", $$1, $$2}'

setup:  ## Create the venv and install dev dependencies
	uv sync --extra dev

lint:  ## Run ruff lint
	uv run ruff check .

format-check:  ## Check formatting without changing files
	uv run ruff format --check .

typecheck:  ## Run mypy
	uv run mypy src/

test:  ## Run mock-mode tests (excludes hardware)
	uv run pytest

test-hardware:  ## Run hardware tests (requires sensors attached)
	uv run pytest -m hardware

run-mock:  ## Run the application in mock mode
	STINGRAY_MODE=mock uv run stingray

preflight:  ## Run the system preflight check
	bash scripts/preflight.sh

clean:  ## Remove caches and build artifacts
	rm -rf .pytest_cache .mypy_cache .ruff_cache build dist *.egg-info
