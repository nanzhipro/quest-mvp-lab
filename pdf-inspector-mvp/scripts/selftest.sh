#!/usr/bin/env bash
# End-to-end self-check: build release, run the page-257 loop condition.
# Usage: scripts/selftest.sh [path/to.pdf]
set -euo pipefail
cd "$(dirname "$0")/.."

PDF="${1:-/Users/nanzhi/Downloads/CH 11 Persistence Monitor.pdf}"

if [ ! -f "$PDF" ]; then
    echo "error: test PDF not found: $PDF" >&2
    exit 2
fi

echo "== building (release) =="
cargo build --release --quiet

echo
echo "== detect =="
./target/release/pdfx detect "$PDF"

echo
echo "== verify (loop condition) =="
./target/release/pdfx verify "$PDF"
