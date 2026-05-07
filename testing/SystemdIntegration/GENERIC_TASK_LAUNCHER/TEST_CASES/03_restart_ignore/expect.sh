# 03_restart_ignore — child has Requires/After + Restart=on-failure; no PartOf.
#
# Semantics to verify:
#   - crash child → child auto-restarts (Restart=on-failure)
#   - stop parent cleanly → child follows (Requires cascade); Restart=on-failure
#     does NOT re-pull the child because clean SIGTERM exits 0 (not a failure).
#
# Note: with Requires=parent present, `systemctl restart parent` also cascades
# to the child via systemd's transaction planner (independently of PartOf).
# So the observable Ignore-vs-Cascade distinction for the dependency axis is
# in the unit file content (PartOf presence), not in runtime behavior when
# Requires= is always emitted. The UnitFileGeneration static tests cover the
# content distinction.

run_scenario() {
    local P=si-03-parent.service
    local C=si-03-child.service

    log "scenario A: start child, expect parent pulled up"
    start "$C"
    wait_state "$P" active 5 || true
    wait_state "$C" active 5 || true
    assert_eq "parent up" "$(active_state "$P")" "active"
    assert_eq "child up"  "$(active_state "$C")" "active"

    log "scenario B: crash child — Restart=on-failure should bring it back"
    local pid_before
    pid_before=$(systemctl --user show "$C" -p MainPID --value)
    crash_unit "$C" || true
    sleep 3
    wait_state "$C" active 5 || true
    local pid_after
    pid_after=$(systemctl --user show "$C" -p MainPID --value)
    assert_eq "child active after auto-restart"    "$(active_state "$C")" "active"
    assert_ne "child fresh PID after auto-restart" "$pid_after" "$pid_before"

    log "scenario C: stop parent cleanly — Requires cascade; Restart=on-failure does not re-pull child (clean exit)"
    stop "$P"
    wait_state "$P" inactive 5 || true
    wait_state "$C" inactive 5 || true
    assert_eq "parent inactive"                                   "$(active_state "$P")" "inactive"
    assert_eq "child inactive after parent stop (Requires cascade)" "$(active_state "$C")" "inactive"
}
