# Use the pyenv `dsa` env instead of letting pipenv create its own venv
export VIRTUAL_ENV := $(HOME)/.pyenv/versions/dsa
export PIPENV_VERBOSITY := -1

.PHONY: install sync clean run

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
