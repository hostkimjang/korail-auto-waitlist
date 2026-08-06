#!/bin/sh
set -eu

# This wrapper is invoked only by isolated browser-test paths. Those containers
# have no external network, capabilities, writable root, or privilege escalation path.
exec /usr/bin/google-chrome --no-sandbox "$@"
