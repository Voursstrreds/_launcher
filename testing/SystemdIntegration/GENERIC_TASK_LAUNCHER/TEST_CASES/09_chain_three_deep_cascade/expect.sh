# 09_chain_three_deep_cascade — restart at top cascades through middle to bottom.
# Verifies that PartOf chains (each level declares its own parent) reach the full depth.

run_scenario() {
    local T=si-09-top.service
    local M=si-09-middle.service
    local B=si-09-bottom.service

    log "scenario A: start bottom, expect chain pulled up"
    start "$B"
    wait_state "$T" active 5 || true
    wait_state "$M" active 5 || true
    wait_state "$B" active 5 || true
    assert_eq "top up"    "$(active_state "$T")" "active"
    assert_eq "middle up" "$(active_state "$M")" "active"
    assert_eq "bottom up" "$(active_state "$B")" "active"

    log "scenario B: restart top — PartOf should cascade through middle to bottom"
    local mid_pid_before bot_pid_before
    mid_pid_before=$(systemctl --user show "$M" -p MainPID --value)
    bot_pid_before=$(systemctl --user show "$B" -p MainPID --value)
    restart "$T"
    sleep 3
    wait_state "$T" active 5 || true
    wait_state "$M" active 5 || true
    wait_state "$B" active 5 || true
    local mid_pid_after bot_pid_after
    mid_pid_after=$(systemctl --user show "$M" -p MainPID --value)
    bot_pid_after=$(systemctl --user show "$B" -p MainPID --value)
    assert_eq "top still active"                                 "$(active_state "$T")" "active"
    assert_eq "middle still active"                              "$(active_state "$M")" "active"
    assert_eq "bottom still active"                              "$(active_state "$B")" "active"
    assert_ne "middle fresh PID (PartOf top)"                    "$mid_pid_after" "$mid_pid_before"
    assert_ne "bottom fresh PID (PartOf middle; chain reached)"  "$bot_pid_after" "$bot_pid_before"
}
