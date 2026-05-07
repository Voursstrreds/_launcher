# 02_abort_cascade — child has Requires/After/PartOf; no Restart block.
#
# Semantics to verify:
#   - restart parent → child follows via PartOf (fresh PID)
#   - crash child → child lands in 'failed' (Abort = no auto-restart)

run_scenario() {
    local P=si-02-parent.service
    local C=si-02-child.service

    log "scenario A: start child, expect parent pulled up"
    start "$C"
    wait_state "$P" active 5 || true
    wait_state "$C" active 5 || true
    assert_eq "parent up" "$(active_state "$P")" "active"
    assert_eq "child up"  "$(active_state "$C")" "active"

    log "scenario B: restart parent — PartOf cascades planned restart to child"
    local child_pid_before
    child_pid_before=$(systemctl --user show "$C" -p MainPID --value)
    restart "$P"
    sleep 2
    wait_state "$P" active 5 || true
    wait_state "$C" active 5 || true
    local child_pid_after
    child_pid_after=$(systemctl --user show "$C" -p MainPID --value)
    assert_eq "child still active after parent restart"      "$(active_state "$C")" "active"
    assert_ne "child fresh PID after parent restart (PartOf)" "$child_pid_after" "$child_pid_before"

    log "scenario C: crash child — Abort means no auto-restart"
    systemctl --user reset-failed "$P" "$C" 2>/dev/null || true
    start "$C"
    wait_state "$C" active 5 || true
    crash_unit "$C" || true
    wait_state "$C" failed 5 || true
    assert_eq "child failed after crash (no auto-restart)" "$(active_state "$C")" "failed"
}
