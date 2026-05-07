# 04_restart_cascade — full combination: Restart block + PartOf edge.
#
# Semantics to verify:
#   - crash child → child auto-restarts (Restart=on-failure)
#   - restart parent → child follows via PartOf (fresh PID)

run_scenario() {
    local P=si-04-parent.service
    local C=si-04-child.service

    log "scenario A: start child, expect parent pulled up"
    start "$C"
    wait_state "$P" active 5 || true
    wait_state "$C" active 5 || true
    assert_eq "parent up" "$(active_state "$P")" "active"
    assert_eq "child up"  "$(active_state "$C")" "active"

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

    log "scenario C: restart parent — Cascade means child follows"
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
}
