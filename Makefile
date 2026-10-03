.PHONY: install check lint format test cov bench data

install:
	pip install -e ".[dev]"

check: lint test

lint:
	ruff check src tests examples benchmarks
	ruff format --check src tests examples benchmarks
	mypy

format:
	ruff format src tests examples benchmarks
	ruff check --fix src tests examples benchmarks

test:
	pytest --cov=rulelens --cov-report=term-missing --cov-fail-under=90

bench:
	python benchmarks/bench.py

data:
	python examples/generate_data.py
