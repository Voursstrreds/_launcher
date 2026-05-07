# 07_two_parents_restart_ignore — child auto-restarts on self-crash; does NOT
# follow parent restarts (no PartOf). Still follows parent clean stop (Requires).

run_scenario() {
    local P1=si-07-parent1.service
    local P2=si-07-parent2.service
    local C=si-07-child.service

    log "scenario A: start child, expect both parents up"
    start "$C"
    wait_state "$P1" active 5 || true
    wait_state "$P2" active 5 || true
    wait_state "$C"  active 5 || true
    assert_eq "parent1 up" "$(active_state "$P1")" "active"
    assert_eq "parent2 up" "$(active_state "$P2")" "active"
    assert_eq "child up"   "$(active_state "$C")"  "active"

    log "scenario B: crash child — Restart=on-failure brings it back"
    local pid_before
    pid_before=$(systemctl --user show "$C" -p MainPID --value)
    crash_unit "$C" || true
    sleep 3
    wait_state "$C" active 5 || true
    local pid_after
    pid_after=$(systemctl --user show "$C" -p MainPID --value)
    assert_eq "child active after auto-restart"    "$(active_state "$C")" "active"
    assert_ne "child fresh PID after auto-restart" "$pid_after" "$pid_before"

    log "scenario C: stop parent1 cleanly — Requires cascade stops child"
    stop "$P1"
    wait_state "$C" inactive 5 || true
    assert_eq "child inactive after parent1 stop (Requires)" "$(active_state "$C")" "inactive"
}
