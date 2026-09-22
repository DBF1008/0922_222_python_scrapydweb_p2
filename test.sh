#!/usr/bin/env bash
# Run all unit test scripts under tests/ one by one for manual testing.
#
# Prerequisites (same as .circleci/config.yml):
#   1. pip install -r requirements.txt && pip install -r requirements-tests.txt
#   2. A Scrapyd server listening on 127.0.0.1:6800 with basic auth admin:12345:
#        printf "[scrapyd]\nusername = admin\npassword = 12345\n" > scrapyd.conf
#        scrapyd
#   3. The logs directory of LogParser, defaults to ~/logs,
#      override it with the env var LOCAL_SCRAPYD_LOGS_DIR if needed.
#
# Usage:
#   bash test.sh                          # run all tests/test_*.py one by one
#   bash test.sh tests/test_deploy.py     # run the specified test script(s) only
#   PYTHON=python3.10 bash test.sh        # override the python interpreter

set -u
cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"
PYTEST_ARGS="${PYTEST_ARGS:--s -vv -l --disable-warnings}"
export LOCAL_SCRAPYD_LOGS_DIR="${LOCAL_SCRAPYD_LOGS_DIR:-$HOME/logs}"

if [ ! -d "$LOCAL_SCRAPYD_LOGS_DIR" ]; then
    echo "LOCAL_SCRAPYD_LOGS_DIR not found: $LOCAL_SCRAPYD_LOGS_DIR"
    echo "Create it or point LOCAL_SCRAPYD_LOGS_DIR to an existing directory."
    exit 1
fi

if [ "$#" -gt 0 ]; then
    TEST_FILES="$@"
else
    TEST_FILES=$(ls tests/test_*.py)
fi

failed_tests=""
for test_file in $TEST_FILES; do
    echo "======================================================================"
    echo "Running $test_file"
    echo "======================================================================"
    if "$PYTHON" -m pytest $PYTEST_ARGS "$test_file"; then
        echo "PASSED: $test_file"
    else
        echo "FAILED: $test_file"
        failed_tests="$failed_tests $test_file"
    fi
done

echo "======================================================================"
if [ -n "$failed_tests" ]; then
    echo "Failed test scripts:"
    for test_file in $failed_tests; do
        echo "  $test_file"
    done
    exit 1
else
    echo "All test scripts passed."
fi
