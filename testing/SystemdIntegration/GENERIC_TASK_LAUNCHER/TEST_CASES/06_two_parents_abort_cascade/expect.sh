# 06_two_parents_abort_cascade — child has PartOf covering both parents.
# Restarting parent1 propagates via PartOf; child gets fresh PID.

run_scenario() {
    local P1=si-06-parent1.service
    local P2=si-06-parent2.service
    local C=si-06-child.service

    log "scenario A: start child, expect both parents up"
    start "$C"
    wait_state "$P1" active 5 || true
    wait_state "$P2" active 5 || true
    wait_state "$C"  active 5 || true
    assert_eq "parent1 up" "$(active_state "$P1")" "active"
    assert_eq "parent2 up" "$(active_state "$P2")" "active"
    assert_eq "child up"   "$(active_state "$C")"  "active"

    log "scenario B: restart parent1 — PartOf on child cascades"
    local child_pid_before
    child_pid_before=$(systemctl --user show "$C" -p MainPID --value)
    restart "$P1"
    sleep 2
    wait_state "$C" active 5 || true
    local child_pid_after
    child_pid_after=$(systemctl --user show "$C" -p MainPID --value)
    assert_eq "child still active after parent1 restart"     "$(active_state "$C")" "active"
    assert_ne "child fresh PID after parent1 restart (PartOf)" "$child_pid_after" "$child_pid_before"

    log "scenario C: crash child — Abort means no auto-restart"
    systemctl --user reset-failed "$P1" "$P2" "$C" 2>/dev/null || true
    start "$C"
    wait_state "$C" active 5 || true
    crash_unit "$C" || true
    wait_state "$C" failed 5 || true
    assert_eq "child failed after crash (no auto-restart)" "$(active_state "$C")" "failed"
}
