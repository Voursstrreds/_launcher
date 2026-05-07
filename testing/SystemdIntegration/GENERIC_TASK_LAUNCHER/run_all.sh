#!/usr/bin/env bash
# Driver: iterate TEST_CASES/*/ in lex order, run each via run_case.sh,
# aggregate verdicts into RESULTS/summary.txt.
set -u

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
CASES_DIR="$ROOT/TEST_CASES"
RESULTS_DIR="$ROOT/RESULTS"
SUMMARY="$RESULTS_DIR/summary.txt"

mkdir -p "$RESULTS_DIR"
: > "$SUMMARY"

printf '%s\n' "============================================================"
printf '%s\n' "SystemdIntegration — GENERIC_TASK_LAUNCHER — driver run"
printf '%s\n' "============================================================"

total=0
fails=0

for dir in "$CASES_DIR"/*/; do
    case_name=$(basename "$dir")
    [[ -f "$dir/input.yaml" && -f "$dir/expect.sh" ]] || continue
    total=$(( total + 1 ))

    printf '\n--- %s ---\n' "$case_name"
    if ! "$ROOT/run_case.sh" "$dir"; then
        printf '[driver] %s run_case.sh exited non-zero\n' "$case_name"
    fi

    verdict_file="$RESULTS_DIR/$case_name.verdict"
    if [[ -r "$verdict_file" ]]; then
        verdict=$(cat "$verdict_file")
    else
        verdict="NO_VERDICT"
    fi
    printf '%-42s %s\n' "$case_name" "$verdict" >> "$SUMMARY"
    [[ "$verdict" == PASS* ]] || fails=$(( fails + 1 ))
done

printf '\n%s\n' "============================================================"
printf 'SUMMARY (from %s)\n' "$SUMMARY"
printf '%s\n' "============================================================"
cat "$SUMMARY"
printf '\nTOTAL: %d   PASSED: %d   FAILED: %d\n' "$total" "$(( total - fails ))" "$fails"

if (( fails > 0 )); then exit 1; fi
exit 0
