"""
DAG Validation — unit tests for GENERIC_TASK_LAUNCHER.

Tests the three DAG validation stages:
    1. Dependency graph      (validate_dag — dep edges)
    2. Group membership graph (validate_dag — group edges)
    3. Unified graph          (validate_unified_dag — merged before-after)

Inputs are pre-normalised YAML files.  For every test case the runner
computes all mappings, draws all three graphs into RESULTS/, and
reports which validation stage (if any) raised a cycle error.

Test categories:
    valid/                 — all three stages pass.
    invalid_dep_cycle/     — cycle in dependency graph.
    invalid_group_cycle/   — cycle in group membership graph.
    invalid_unified_cycle/ — deps and groups individually acyclic,
                             but the merged structure has a cycle.
"""

import sys
import os
import yaml

# ---------------------------------------------------------------------------
# Path setup.
# ---------------------------------------------------------------------------
LAUNCHER_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), '..', '..', '..', 'Codebase', 'LAUNCHER')
)
sys.path.insert(0, LAUNCHER_DIR)

from RelationshipMapping import (
    compute_before, compute_group_maps, validate_dag,
    compute_unified_before, validate_unified_dag,
)
from GraphVisualizer import draw_dependency_graph, draw_group_graph

# ---------------------------------------------------------------------------
# Field names for GENERIC_TASK_LAUNCHER.
# ---------------------------------------------------------------------------
DEPENDS_FIELD = 'Depends'
GROUP_FIELD   = 'Group'
MEMBERS_FIELD = 'Members'

# ---------------------------------------------------------------------------
# Directory constants.
# ---------------------------------------------------------------------------
TEST_DIR    = os.path.dirname(os.path.abspath(__file__))
CASES_DIR   = os.path.join(TEST_DIR, 'TEST_CASES')
RESULTS_DIR = os.path.join(TEST_DIR, 'RESULTS')

CATEGORIES = [
    ('valid',                 'PASS'),
    ('invalid_dep_cycle',     'FAIL at dep validation'),
    ('invalid_group_cycle',   'FAIL at group validation'),
    ('invalid_unified_cycle', 'FAIL at unified validation'),
]


def collect_yaml_files(directory: str) -> list[str]:
    if not os.path.isdir(directory):
        return []
    return sorted(
        os.path.join(directory, f)
        for f in os.listdir(directory)
        if f.endswith('.yaml')
    )


def load_input(yaml_path: str) -> dict:
    with open(yaml_path) as f:
        return yaml.safe_load(f)


def draw_all_graphs(
    before_map:     dict[str, list[str]],
    group_of:       dict[str, list[str]],
    members_of:     dict[str, list[str]],
    unified_before: dict[str, list[str]],
    output_dir:     str,
) -> None:
    """Draws dependency, group, and unified graphs into output_dir."""
    draw_dependency_graph(before_map, output_dir)
    draw_group_graph(group_of, members_of, output_dir)
    draw_dependency_graph(
        unified_before, output_dir,
        graph_name='unified_graph',
        graph_label='Unified Dependency + Group Graph',
    )


def run_case(yaml_path: str, results_dir: str) -> tuple[str, str, str]:
    """
    Runs the full mapping + validation pipeline on one YAML file.

    Always computes all mappings and draws all graphs.
    Catches SystemExit from each validation stage separately.

    Returns (filename, actual_outcome, detail_text).
    actual_outcome is one of:
        'PASS'
        'FAIL at dep validation'
        'FAIL at group validation'
        'FAIL at unified validation'
    """
    filename = os.path.basename(yaml_path)
    case_results_dir = os.path.join(results_dir, filename.replace('.yaml', ''))
    all_instances = load_input(yaml_path)

    # --- Always compute all mappings ---
    before_map = compute_before(all_instances, DEPENDS_FIELD)
    group_of, members_of = compute_group_maps(
        all_instances, GROUP_FIELD, MEMBERS_FIELD,
    )
    unified_before = compute_unified_before(before_map, group_of)

    # --- Always draw all graphs ---
    draw_all_graphs(before_map, group_of, members_of, unified_before, case_results_dir)

    # --- Build output text ---
    lines = []
    lines.append(f"Input: {filename}")
    lines.append(f"Instances: {list(all_instances.keys())}")

    lines.append("")
    lines.append("Dependency (before_map):")
    for key in all_instances:
        dep = all_instances[key].get(DEPENDS_FIELD, [])
        bef = before_map[key]
        lines.append(f"  {key:10s}  Depends: {str(dep):30s}  before: {bef}")

    lines.append("")
    lines.append("Group membership:")
    for key in all_instances:
        grp = group_of[key]
        mem = members_of[key]
        lines.append(f"  {key:10s}  group_of: {str(grp):30s}  members_of: {mem}")

    lines.append("")
    lines.append("Unified (before_map + group_of):")
    for key in all_instances:
        lines.append(f"  {key:10s}  unified_before: {unified_before[key]}")

    # --- Run validations, catch failures ---
    outcome = 'PASS'
    cycle_msg = None

    # Stage 1 + 2: dep and group validation
    try:
        validate_dag(all_instances, DEPENDS_FIELD, GROUP_FIELD, MEMBERS_FIELD)
    except SystemExit as e:
        cycle_msg = str(e)
        if 'Dependency' in cycle_msg:
            outcome = 'FAIL at dep validation'
        else:
            outcome = 'FAIL at group validation'

    # Stage 3: unified validation (only if stages 1+2 passed)
    if outcome == 'PASS':
        try:
            validate_unified_dag(unified_before)
        except SystemExit as e:
            cycle_msg = str(e)
            outcome = 'FAIL at unified validation'

    lines.append("")
    lines.append(f"Outcome: {outcome}")
    if cycle_msg:
        lines.append(f"Cycle:   {cycle_msg}")

    detail = '\n'.join(lines)

    # --- Write result file ---
    result_path = os.path.join(case_results_dir, 'result.txt')
    with open(result_path, 'w') as f:
        f.write(detail + '\n')

    return filename, outcome, detail


def main() -> int:
    passed = 0
    failed = 0
    total  = 0

    for category, expected_outcome in CATEGORIES:
        cases_dir   = os.path.join(CASES_DIR, category)
        results_dir = os.path.join(RESULTS_DIR, category)
        yaml_files  = collect_yaml_files(cases_dir)

        if not yaml_files:
            continue

        print("=" * 60)
        print(f"CATEGORY: {category}  (expect: {expected_outcome})")
        print("=" * 60)

        for yaml_path in yaml_files:
            total += 1
            filename, outcome, detail = run_case(yaml_path, results_dir)

            if outcome == expected_outcome:
                status = "PASS"
                passed += 1
            else:
                status = f"FAIL — expected '{expected_outcome}', got '{outcome}'"
                failed += 1

            print(f"\n  [{status}] {filename}")
            print(f"    {detail.replace(chr(10), chr(10) + '    ')}")

    print()
    print("=" * 60)
    print(f"TOTAL: {total}  PASSED: {passed}  FAILED: {failed}")
    print("=" * 60)

    return 0 if failed == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
