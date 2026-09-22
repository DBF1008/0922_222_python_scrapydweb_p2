#!/bin/bash
# Manual test entry for ScrapydWeb.
#
# Usage:
#   bash test.sh          # Run unit tests which do NOT require a running Scrapyd server
#   bash test.sh all      # Run the whole test suite (requires Scrapyd at 127.0.0.1:6800,
#                         # see custom_settings['_SCRAPYD_SERVER'] in tests/conftest.py)
#   bash test.sh <file>   # Run a specific test file, e.g. bash test.sh tests/test_deploy.py
set -e
cd "$(dirname "$0")"

export SCRAPYDWEB_TESTMODE=True

# Override the interpreter via: PYTHON=/path/to/python bash test.sh
PYTHON="${PYTHON:-python3}"
if ! command -v "$PYTHON" >/dev/null 2>&1; then
    PYTHON=python
fi
if ! "$PYTHON" -m pytest --version >/dev/null 2>&1; then
    echo "pytest not found for '$PYTHON', try: PYTHON=/path/to/python bash test.sh" >&2
    exit 1
fi

if [ "$1" = "all" ]; then
    "$PYTHON" -m pytest -s -vv -l --disable-warnings tests
elif [ -n "$1" ]; then
    "$PYTHON" -m pytest -s -vv -l --disable-warnings "$1"
else
    "$PYTHON" -m pytest -s -vv -l --disable-warnings tests/test_deploy_uncompress.py
fi
