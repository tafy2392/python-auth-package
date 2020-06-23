#!/bin/bash

ANSI_RED='\033[0;31m'
ANSI_GREEN='\033[0;32m'
ANSI_RESET='\033[0m'

# This is a function to better handle paths that may contains whitespace.
lint() {
    # Track failures so we can avoid returning early.
    failed=0

    flake8 "$@" || failed=1
    mypy --warn-unreachable "$@" || failed=1
    isort -c -rc "$@" || failed=1
    black --check -l79 "$@" || failed=1
    return $failed
}

lint src/marathon_acme_trio/ tests/
result=$?

if [ $result = 0 ]; then
    echo -e "${ANSI_GREEN}Lint passed!${ANSI_RESET}"
else
    echo -e "${ANSI_RED}Lint failed!${ANSI_RESET}"
fi

exit $result
