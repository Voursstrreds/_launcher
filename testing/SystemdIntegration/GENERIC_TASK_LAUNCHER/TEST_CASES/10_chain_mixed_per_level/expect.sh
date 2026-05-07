# 10_chain_mixed_per_level — three-deep chain, different DependencyBehavior
# at each level.
#
# Chain: top ← middle (Ignore, Requires+After only) ← bottom (Cascade, +PartOf).
#
# Runtime observations under the emitted directive sets:
#   - restart top:      Requires cascade (transaction-scoped) pulls middle
#                       down and back; bottom follows via its Requires+PartOf.
#   - restart middle:   bottom follows via PartOf=middle.  Top is unaffected
#                       (middle is child of top, not vice versa).
#   - stop top:         middle and bottom cascade to inactive via Requires.
#
# This case exercises mixed per-level emission and confirms the scenario runs
# cleanly with heterogeneous DependencyBehavior values across the chain.

run_scenario() {
    local T=si-10-top.service
    local M=si-10-middle.service
    local B=si-10-bottom.service

    log "scenario A: start bottom, expect chain pulled up"
    start "$B"
    wait_state "$T" active 5 || true
    wait_state "$M" active 5 || true
    wait_state "$B" active 5 || true
    assert_eq "top up"    "$(active_state "$T")" "active"
    assert_eq "middle up" "$(active_state "$M")" "active"
    assert_eq "bottom up" "$(active_state "$B")" "active"

    log "scenario B: restart middle — bottom follows via PartOf=middle"
    local bot_pid_before
    bot_pid_before=$(systemctl --user show "$B" -p MainPID --value)
    restart "$M"
    sleep 3
    wait_state "$M" active 5 || true
    wait_state "$B" active 5 || true
    local bot_pid_after
    bot_pid_after=$(systemctl --user show "$B" -p MainPID --value)
    assert_eq "middle still active after restart"            "$(active_state "$M")" "active"
    assert_eq "bottom still active after middle restart"     "$(active_state "$B")" "active"
    assert_ne "bottom fresh PID (PartOf=middle propagates)"  "$bot_pid_after" "$bot_pid_before"

    log "scenario C: stop top cleanly — chain cascades down (Requires)"
    stop "$T"
    wait_state "$T" inactive 5 || true
    wait_state "$M" inactive 5 || true
    wait_state "$B" inactive 5 || true
    assert_eq "top inactive"    "$(active_state "$T")" "inactive"
    assert_eq "middle inactive" "$(active_state "$M")" "inactive"
    assert_eq "bottom inactive" "$(active_state "$B")" "inactive"
}
