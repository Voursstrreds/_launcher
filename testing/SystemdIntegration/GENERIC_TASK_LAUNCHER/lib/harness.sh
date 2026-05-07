# Shared helpers for SystemdIntegration/GENERIC_TASK_LAUNCHER test scripts.
# Sourced, not executed. Adapted from Utilities/SystemdUnitsTesting/lib/harness.sh
# — only the ROOT/RESULTS_DIR layout differs.
#
# Required globals from the sourcing script:
#   THIS_DIR     absolute path to the test case directory
#   CASE_NAME    basename of THIS_DIR
#
# Conventions:
#   - All systemd operations use --user.
#   - Unit files are installed to ~/.config/systemd/user/.
#   - Results land under ROOT/RESULTS/<CASE_NAME>.{verdict,journal,log,status.log}.

HARNESS_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd -- "$HARNESS_DIR/.." && pwd)
SYSTEMD_USER_DIR="$HOME/.config/systemd/user"
RESULTS_DIR="$ROOT/RESULTS"
mkdir -p "$SYSTEMD_USER_DIR" "$RESULTS_DIR"

FAIL_COUNT=0
CHECK_COUNT=0
INSTALLED_UNITS=()

log() {
    printf '[%s] %s\n' "$(date '+%H:%M:%S')" "$*"
}

install_units() {
    local src_dir=$1
    local f
    for f in "$src_dir"/*.service "$src_dir"/*.target; do
        [[ -e "$f" ]] || continue
        cp "$f" "$SYSTEMD_USER_DIR/"
        INSTALLED_UNITS+=("$(basename "$f")")
    done
    systemctl --user daemon-reload
    log "installed: ${INSTALLED_UNITS[*]}"
    : > "$RESULTS_DIR/$CASE_NAME.status.log"
    snapshot "installed + daemon-reload (initial state)"
}

snapshot() {
    local label="$1"
    local out="$RESULTS_DIR/$CASE_NAME.status.log"
    if (( ${#INSTALLED_UNITS[@]} == 0 )); then
        return
    fi
    local pattern
    pattern=$(IFS='|'; echo "${INSTALLED_UNITS[*]}")
    pattern="${pattern//./\\.}"
    {
        printf '=== [%s]  %s ===\n' "$label" "$(date '+%H:%M:%S.%3N')"
        printf '$ systemctl --user list-units --all | grep -E %q\n' "$pattern"
        systemctl --user list-units --all --no-legend --no-pager --plain 2>/dev/null \
            | grep -E "$pattern" \
            || echo '(no matching units loaded)'
        printf '\n'
    } >> "$out"
}

uninstall_units() {
    local u
    for u in "${INSTALLED_UNITS[@]-}"; do
        [[ -n "$u" ]] || continue
        systemctl --user stop    "$u" 2>/dev/null || true
        systemctl --user reset-failed "$u" 2>/dev/null || true
        rm -f "$SYSTEMD_USER_DIR/$u"
    done
    systemctl --user daemon-reload 2>/dev/null || true
    INSTALLED_UNITS=()
}

start() {
    snapshot "before start $1"
    systemctl --user start "$1"
    snapshot "after  start $1"
}
stop() {
    snapshot "before stop $1"
    systemctl --user stop "$1"
    snapshot "after  stop $1"
}
restart() {
    snapshot "before restart $1"
    systemctl --user restart "$1"
    snapshot "after  restart $1"
}

kill_signal() {
    local unit=$1 sig=$2
    snapshot "before kill -s $sig $unit"
    systemctl --user kill -s "$sig" "$unit"
    snapshot "after  kill -s $sig $unit"
}

state() {
    local unit=$1
    systemctl --user show "$unit" -p ActiveState -p SubState -p Result --value | paste -sd' ' -
}

active_state() {
    systemctl --user show "$1" -p ActiveState --value
}

sub_state() {
    systemctl --user show "$1" -p SubState --value
}

result_state() {
    systemctl --user show "$1" -p Result --value
}

wait_state() {
    local unit=$1 expected=$2 timeout=${3:-5}
    local deadline=$(( $(date +%s) + timeout ))
    local current
    while [[ $(date +%s) -lt $deadline ]]; do
        current=$(active_state "$unit")
        if [[ "$current" == "$expected" ]]; then
            return 0
        fi
        sleep 0.2
    done
    return 1
}

wait_substate() {
    local unit=$1 expected=$2 timeout=${3:-5}
    local deadline=$(( $(date +%s) + timeout ))
    local current
    while [[ $(date +%s) -lt $deadline ]]; do
        current=$(sub_state "$unit")
        if [[ "$current" == "$expected" ]]; then
            return 0
        fi
        sleep 0.2
    done
    return 1
}

journal_window() {
    local unit=$1 since=$2
    journalctl --user -u "$unit" --since="$since" --no-pager 2>/dev/null || true
}

count_restarts() {
    local unit=$1 since=$2
    journal_window "$unit" "$since" | grep -c -E 'Scheduled restart job|Started' || true
}

crash_unit() {
    local unit=$1
    local pid
    pid=$(systemctl --user show "$unit" -p MainPID --value)
    if [[ -z "$pid" || "$pid" == "0" ]]; then
        log "crash_unit: $unit has no MainPID"
        return 1
    fi
    snapshot "before crash (SIGSEGV to $unit pid=$pid)"
    kill -SEGV "$pid"
    log "crash_unit: sent SIGSEGV to $unit (pid $pid)"
    sleep 0.5
    snapshot "after  crash (SIGSEGV to $unit pid=$pid)"
}

assert_eq() {
    local label=$1 got=$2 want=$3
    CHECK_COUNT=$(( CHECK_COUNT + 1 ))
    if [[ "$got" == "$want" ]]; then
        log "  PASS  $label  (got=$got)"
    else
        FAIL_COUNT=$(( FAIL_COUNT + 1 ))
        log "  FAIL  $label  got='$got'  want='$want'"
    fi
}

assert_ne() {
    local label=$1 got=$2 unwanted=$3
    CHECK_COUNT=$(( CHECK_COUNT + 1 ))
    if [[ "$got" != "$unwanted" ]]; then
        log "  PASS  $label  (got=$got)"
    else
        FAIL_COUNT=$(( FAIL_COUNT + 1 ))
        log "  FAIL  $label  got='$got' should not equal '$unwanted'"
    fi
}

record_verdict() {
    local case_name=$1
    local verdict_file="$RESULTS_DIR/$case_name.verdict"
    if (( FAIL_COUNT == 0 )); then
        echo "PASS ($CHECK_COUNT checks)" > "$verdict_file"
    else
        echo "FAIL ($FAIL_COUNT/$CHECK_COUNT checks)" > "$verdict_file"
    fi
    log "verdict: $(cat "$verdict_file")"
}

cleanup_on_exit() {
    local rc=$?
    trap - EXIT
    snapshot "final state before uninstall"
    uninstall_units
    return $rc
}
