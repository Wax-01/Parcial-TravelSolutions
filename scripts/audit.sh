#!/usr/bin/env bash
# Supply-chain audit: pip-audit for every Python component (pinned + hashed requirements) and npm audit for the frontend.
# Reports are written to docs/seguridad/. Exit code != 0 if any vulnerability is found.
set -u
cd "$(dirname "$0")/.."
OUT=docs/seguridad
mkdir -p "$OUT"
status=0
COMPONENTS="services/gateway services/orders services/flights services/hotels services/cars ingestion db"

echo "== pip-audit (Python 3.12 container) =="
MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd -W 2>/dev/null || pwd):/w" -w /w python:3.12-slim sh -c '
  pip install -q pip-audit >/dev/null 2>&1
  echo "pip-audit $(pip-audit --version | cut -d" " -f2) - $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  rc=0
  for c in '"$COMPONENTS"'; do
    echo; echo "### $c/requirements.txt"
    pip-audit -r $c/requirements.txt --require-hashes --no-deps --disable-pip --progress-spinner off 2>&1 || rc=1
  done
  exit $rc' | tee "$OUT/pip-audit.txt"
[ "${PIPESTATUS[0]}" -ne 0 ] && status=1

echo; echo "== npm audit (frontend) =="
( cd frontend && npm audit --package-lock-only 2>&1 ) | tee "$OUT/npm-audit.txt"
[ "${PIPESTATUS[0]}" -ne 0 ] && status=1

echo; [ $status -eq 0 ] && echo "AUDIT CLEAN" || echo "AUDIT FOUND ISSUES (see $OUT)"
exit $status
