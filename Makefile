# Loads .env if present, so `make run` picks up API keys and DATA_DIR.
-include .env
export

PY ?= .venv/bin/python

.PHONY: install demo run dashboard test docker-demo

install:
	python3 -m venv .venv
	$(PY) -m pip install -q -r requirements-dev.txt
	cd dashboard && npm ci

demo:            ## synthetic data, no API keys
	$(PY) -m sentiment seed-demo

run:             ## one real collection + judging run
	$(PY) -m sentiment run

dashboard:
	cd dashboard && node server.js

test:
	$(PY) -m pytest -q
	cd dashboard && npm test

docker-demo:
	docker compose run --rm pipeline python -m sentiment seed-demo
	docker compose up dashboard
