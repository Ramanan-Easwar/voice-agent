# Use the pyenv `dsa` env instead of letting pipenv create its own venv
export VIRTUAL_ENV := $(HOME)/.pyenv/versions/dsa
export PIPENV_VERBOSITY := -1

.PHONY: install sync clean run stats

# Resolve the Pipfile, install, and update Pipfile.lock
install:
	pipenv install

# Install exactly what Pipfile.lock pins (no re-resolving)
sync:
	pipenv sync

# Uninstall anything in the env that isn't in Pipfile.lock
clean:
	pipenv clean

run:
	pipenv run python bot.py

# p50/p95 and per-stage averages from the last session's latency.jsonl
stats:
	pipenv run python scripts/latency_stats.py latency.jsonl
