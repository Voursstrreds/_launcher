#!/usr/bin/env bash
# Per-case runner for the GENERIC_TASK_LAUNCHER SystemdIntegration bench.
#
# Usage: run_case.sh <case_dir>
#
# Per-case flow:
#   1. Invoke UnitGenerator.py on <case_dir>/input.yaml. Generated *.service /
#      *.target files and manifest.ini go to /tmp/gtl-si-<case>/.
#   2. Source lib/harness.sh + <case_dir>/expect.sh.
#   3. Install the generated unit files (snapshot before/after every op).
#   4. Call the scenario's run_scenario() (defined in expect.sh).
#   5. Dump per-unit journal windows + record_verdict.
#   6. Trap-driven cleanup_on_exit uninstalls units and daemon-reloads.
set -u

if [[ $# -lt 1 ]]; then
    printf 'usage: %s <case_dir>\n' "$0" >&2
    exit 2
fi

CASE_DIR=$(cd -- "$1" && pwd)
BENCH_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
PROJECT_ROOT=$(cd -- "$BENCH_DIR/../../.." && pwd)
LAUNCHER_DIR="$PROJECT_ROOT/Codebase/LAUNCHER"
PYTHON="$PROJECT_ROOT/venv/bin/python3"

CASE_NAME=$(basename "$CASE_DIR")
THIS_DIR="$CASE_DIR"   # harness uses this as the scope for snapshots

source "$BENCH_DIR/lib/harness.sh"

START_TS=$(date '+%Y-%m-%d %H:%M:%S')

# -----------------------------------------------------------------------------
# Step 1 — Run UnitGenerator to generate unit files + manifest from input.yaml.
# -----------------------------------------------------------------------------
PARSE_OUT="/tmp/gtl-si-$CASE_NAME"
rm -rf "$PARSE_OUT"
mkdir -p "$PARSE_OUT"

log "parsing $CASE_DIR/input.yaml  ->  $PARSE_OUT/"

(
    cd "$LAUNCHER_DIR" && \
    "$PYTHON" UnitGenerator.py \
        "INPUT_FILE=$CASE_DIR/input.yaml" \
        "UNIT_FILE_OUTPUT_PATH=$PARSE_OUT/" \
        "MANIFEST_FILE_PATH=$PARSE_OUT/manifest.ini"
) > "$RESULTS_DIR/$CASE_NAME.parse.log" 2>&1

if [[ $? -ne 0 ]]; then
    log "parse FAILED — see $RESULTS_DIR/$CASE_NAME.parse.log"
    echo "FAIL (parse error)" > "$RESULTS_DIR/$CASE_NAME.verdict"
    exit 1
fi

# -----------------------------------------------------------------------------
# Step 2 — Install generated units; arm cleanup trap.
# -----------------------------------------------------------------------------
trap cleanup_on_exit EXIT
install_units "$PARSE_OUT"

# -----------------------------------------------------------------------------
# Step 3 — Source the case's scenario and run it.
# -----------------------------------------------------------------------------
if [[ ! -r "$CASE_DIR/expect.sh" ]]; then
    log "missing expect.sh in $CASE_DIR"
    echo "FAIL (missing expect.sh)" > "$RESULTS_DIR/$CASE_NAME.verdict"
    exit 1
fi

source "$CASE_DIR/expect.sh"

if ! declare -F run_scenario > /dev/null; then
    log "expect.sh did not define run_scenario()"
    echo "FAIL (no run_scenario)" > "$RESULTS_DIR/$CASE_NAME.verdict"
    exit 1
fi

log "running scenario for $CASE_NAME"
run_scenario

# -----------------------------------------------------------------------------
# Step 4 — Dump per-unit journal windows + record verdict.
# -----------------------------------------------------------------------------
for u in "${INSTALLED_UNITS[@]}"; do
    journal_window "$u" "$START_TS" > "$RESULTS_DIR/$CASE_NAME.$u.journal"
done

record_verdict "$CASE_NAME"

# cleanup_on_exit fires via EXIT trap.
