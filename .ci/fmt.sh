#!/bin/sh

# This is a function to better handle paths that may contains whitespace.
fmt() {
    isort -rc "$@"
    black -l79 "$@"
}

fmt src/marathon_acme_trio/ tests/
