#!/usr/bin/env bash
# Run the pytest suite with coverage, then gate CRAP per function, total coverage and changed-line coverage.
set -euo pipefail

base="${CRAP_GATE_BASE:-}"
if [[ -z ${base} ]] && ! base="$(git symbolic-ref --quiet --short refs/remotes/origin/HEAD)"; then
  echo 'crap-gate: origin/HEAD is not set. Run git remote set-head origin --auto, or set CRAP_GATE_BASE.' >&2
  exit 2
fi
uv run pytest -q --cov --cov-branch --cov-report=json:coverage.json --cov-report=xml:coverage.xml
uv run ruff check src --select C901 --config 'lint.mccabe.max-complexity=0' --output-format json --exit-zero >ruff.json
uv run --no-project tools/crap_gate.py crap --ruff-json ruff.json --python-coverage coverage.json \
  --baseline crap-baseline.json
uvx diff-cover@10.6.0 coverage.xml --compare-branch="${base}" --fail-under=95
