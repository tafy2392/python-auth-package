#!/usr/bin/env bash
set -eo pipefail

PEBBLE_BASE_URL='https://github.com/letsencrypt/pebble/releases/download'
PEBBLE_ARCH="${PEBBLE_ARCH:-linux-amd64}"
PEBBLE_VERSION="${PEBBLE_VERSION:-2.3.0}"

curl -fsSL "${PEBBLE_BASE_URL}/v${PEBBLE_VERSION}/pebble_${PEBBLE_ARCH}" > pebble
curl -fsSL "${PEBBLE_BASE_URL}/v${PEBBLE_VERSION}/pebble-challtestsrv_${PEBBLE_ARCH}" > pebble-challtestsrv

chmod +x pebble pebble-challtestsrv
