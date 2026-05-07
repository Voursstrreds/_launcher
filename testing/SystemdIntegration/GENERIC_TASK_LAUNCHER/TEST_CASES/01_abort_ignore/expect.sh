# 01_abort_ignore — child has only Requires/After on parent; no PartOf; no Restart block.
#
# Semantics to verify:
#   - start child → parent pulled up via Requires=
#   - stop parent cleanly → child follows (Requires cascades PLANNED stop)
#   - crash child → child lands in 'failed' (Abort = no auto-restart)

run_scenario() {
    local P=si-01-parent.service
    local C=si-01-child.service

    log "scenario A: start child, expect parent pulled up"
    start "$C"
    wait_state "$P" active 5 || true
    wait_state "$C" active 5 || true
    assert_eq "parent up after starting child" "$(active_state "$P")" "active"
    assert_eq "child up"                       "$(active_state "$C")" "active"

    log "scenario B: stop parent cleanly — Requires cascades planned stop"
    stop "$P"
    wait_state "$P" inactive 5 || true
    wait_state "$C" inactive 5 || true
    assert_eq "parent inactive"                        "$(active_state "$P")" "inactive"
    assert_eq "child cascades to inactive (Requires)"  "$(active_state "$C")" "inactive"

    log "scenario C: restart child; crash it — Abort = no auto-restart"
    systemctl --user reset-failed "$P" "$C" 2>/dev/null || true
    start "$C"
    wait_state "$C" active 5 || true
    crash_unit "$C" || true
    wait_state "$C" failed 5 || true
    assert_eq "child failed after crash (no auto-restart)" "$(active_state "$C")" "failed"
}
