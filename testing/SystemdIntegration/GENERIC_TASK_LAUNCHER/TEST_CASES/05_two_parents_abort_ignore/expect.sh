# 05_two_parents_abort_ignore — child with two parents; verifies uniform
# per-child emission (Requires covers BOTH parents) and that stopping either
# parent cascades to child.

run_scenario() {
    local P1=si-05-parent1.service
    local P2=si-05-parent2.service
    local C=si-05-child.service

    log "scenario A: start child, expect BOTH parents pulled up"
    start "$C"
    wait_state "$P1" active 5 || true
    wait_state "$P2" active 5 || true
    wait_state "$C"  active 5 || true
    assert_eq "parent1 up" "$(active_state "$P1")" "active"
    assert_eq "parent2 up" "$(active_state "$P2")" "active"
    assert_eq "child up"   "$(active_state "$C")"  "active"

    log "scenario B: stop parent1 — Requires cascade brings child down"
    stop "$P1"
    wait_state "$C" inactive 5 || true
    assert_eq "parent1 inactive"                   "$(active_state "$P1")" "inactive"
    assert_eq "child inactive after parent1 stop"  "$(active_state "$C")"  "inactive"

    log "scenario C: restart from clean state; crash child — no auto-restart"
    systemctl --user reset-failed "$P1" "$P2" "$C" 2>/dev/null || true
    start "$C"
    wait_state "$C" active 5 || true
    crash_unit "$C" || true
    wait_state "$C" failed 5 || true
    assert_eq "child failed after crash (no auto-restart)" "$(active_state "$C")" "failed"
}
