#!/usr/bin/env bash
# Run every check a push must pass against a snapshot of HEAD.
#
# The checks run in a throwaway git worktree of HEAD, so editing the real
# tree while they run cannot reach them, and what is verified is exactly
# what a push would publish: commits, never uncommitted work. Commit first,
# then gate.
#
# The quick checks start at once beside pytest, one core each. pytest runs
# on one core, as test.yml runs it: spread over a Raspberry Pi's cores, the
# Pidpa card renders ran past the 30 second test timeout. The two mypy runs
# keep a cache each under tmp/ in the main checkout, which outlives the
# worktree.
#
# The interpreter is GATE_PYTHON, else .venv/bin/python in the checkout:
# any with requirements-dev.txt installed. The workflow linter runs when an
# actionlint binary sits at tmp/actionlint.
#
# Usage: scripts/gate.sh [pytest args...]
#   scripts/gate.sh                      the whole suite, as test.yml runs it
#   scripts/gate.sh tests/test_pidpa.py  one file, for a quick pass
set -u

ROOT=$(git rev-parse --show-toplevel) || exit 1
cd "$ROOT" || exit 1
PYTHON=${GATE_PYTHON:-$ROOT/.venv/bin/python}
ACTIONLINT="$ROOT/tmp/actionlint"
SHA=$(git rev-parse --short HEAD)
WORKTREE="$ROOT/tmp/gate/$SHA.$$"
LOGS="$ROOT/tmp/gate/$SHA.$$.logs"

[ -x "$PYTHON" ] || { echo "no interpreter at $PYTHON; set GATE_PYTHON" >&2; exit 1; }

cleanup() {
  cd "$ROOT" || return
  git worktree remove --force "$WORKTREE" >/dev/null 2>&1
  rm -rf "$LOGS"
}
# An interrupt stops the checks before the worktree goes. They run in the
# background of a script, so they ignore SIGINT and would go on in a
# deleted directory while the script waited on them.
stop() {
  local pid
  for pid in ${pids[@]+"${pids[@]}"}; do
    pkill -TERM -P "$pid" 2>/dev/null
    kill -TERM "$pid" 2>/dev/null
  done
  exit "$1"
}
trap cleanup EXIT
trap 'stop 130' INT
trap 'stop 143' TERM

mkdir -p "$LOGS"
git worktree add --detach --quiet "$WORKTREE" HEAD || exit 1
echo "gating $SHA in $WORKTREE"
echo

names=()
pids=()
# Start one check in the background, its output in LOGS under its position.
start() {
  local n=${#names[@]}
  names+=("$1")
  shift
  ( cd "$WORKTREE" && "$@" ) > "$LOGS/$n.log" 2>&1 &
  pids+=($!)
}

start "pytest" "$PYTHON" -m pytest "${@:-tests/}" -q
start "ruff check" "$PYTHON" -m ruff check .
start "ruff format" "$PYTHON" -m ruff format --check .
start "mypy strict" "$PYTHON" -m mypy --cache-dir "$ROOT/tmp/mypy_strict" \
  --strict custom_components/be_water_prices
start "mypy scripts" "$PYTHON" -m mypy --cache-dir "$ROOT/tmp/mypy_scripts" \
  --strict scripts
if [ -x "$ACTIONLINT" ]; then
  # Globbed, so a workflow added later cannot slip past by not being named.
  start "actionlint" bash -c "\"$ACTIONLINT\" .github/workflows/*.yml"
else
  echo "== actionlint"
  echo "   skipped: no binary at $ACTIONLINT"
  echo
fi

failed=""
# pytest started first and is reported last, after the quick checks.
order=("${!names[@]}")
order=("${order[@]:1}" 0)
for n in "${order[@]}"; do
  wait "${pids[$n]}"
  rc=$?
  echo "== ${names[$n]}"
  cat "$LOGS/$n.log"
  if [ "$rc" -eq 0 ]; then
    echo "   ok"
  else
    echo "   FAILED"
    failed="$failed ${names[$n]// /-}"
  fi
  echo
done
if [ -n "$failed" ]; then
  echo "GATE FAILED:$failed"
  exit 1
fi
echo "gate passed on $SHA"
