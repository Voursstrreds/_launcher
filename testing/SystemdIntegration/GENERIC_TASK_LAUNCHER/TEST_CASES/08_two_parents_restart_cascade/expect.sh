# 08_two_parents_restart_cascade — full combination, multi-parent.

run_scenario() {
    local P1=si-08-parent1.service
    local P2=si-08-parent2.service
    local C=si-08-child.service

    log "scenario A: start child, expect both parents up"
    start "$C"
    wait_state "$P1" active 5 || true
    wait_state "$P2" active 5 || true
    wait_state "$C"  active 5 || true
    assert_eq "parent1 up" "$(active_state "$P1")" "active"
    assert_eq "parent2 up" "$(active_state "$P2")" "active"
    assert_eq "child up"   "$(active_state "$C")"  "active"

    log "scenario B: crash child — Restart=on-failure"
    local pid_before
    pid_before=$(systemctl --user show "$C" -p MainPID --value)
    crash_unit "$C" || true
    sleep 3
    wait_state "$C" active 5 || true
    local pid_after
    pid_after=$(systemctl --user show "$C" -p MainPID --value)
    assert_eq "child active after auto-restart"    "$(active_state "$C")" "active"
    assert_ne "child fresh PID after auto-restart" "$pid_after" "$pid_before"

    log "scenario C: restart parent2 — PartOf cascades"
    local child_pid_before
    child_pid_before=$(systemctl --user show "$C" -p MainPID --value)
    restart "$P2"
    sleep 2
    wait_state "$C" active 5 || true
    local child_pid_after
    child_pid_after=$(systemctl --user show "$C" -p MainPID --value)
    assert_eq "child still active after parent2 restart"     "$(active_state "$C")" "active"
    assert_ne "child fresh PID after parent2 restart (PartOf)" "$child_pid_after" "$child_pid_before"
}
