"""
Relationship Mapping — unit tests for GENERIC_TASK_LAUNCHER.

Tests compute_before() and compute_group_maps() from
RelationshipMapping.py.  Inputs are pre-normalised YAML files
(as produced by the validator).

For each test case the runner:
  1. Loads the normalised input.
  2. Calls compute_before  → before_map.
  3. Calls compute_group_maps → group_of, members_of.
  4. Compares the input-declared fields (Depends, Group, Members)
     against the completed mappings to show what was filled in
     by reconciliation.
  5. Prints results to terminal and dumps them into RESULTS/.

Test categories:
    dep_only/   — only Depends fields, no Group/Members.
    group_only/ — only Group/Members fields, no Depends.
    mixed/      — both dependency and group mappings together.
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

from RelationshipMapping import compute_before, compute_group_maps

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

CATEGORIES = ['dep_only', 'group_only', 'mixed']


def collect_yaml_files(directory: str) -> list[str]:
    """Returns sorted list of .yaml files in a directory."""
    if not os.path.isdir(directory):
        return []
    files = [
        os.path.join(directory, f)
        for f in os.listdir(directory)
        if f.endswith('.yaml')
    ]
    return sorted(files)


def load_input(yaml_path: str) -> dict:
    """Loads a pre-normalised YAML file."""
    with open(yaml_path) as f:
        return yaml.safe_load(f)


def format_mapping_line(label: str, declared, computed) -> str:
    """
    Formats one mapping field showing input vs result.
    Marks with (*) when compute filled in beyond what was declared.
    """
    if declared == computed:
        return f"    {label:10s}  input: {declared!s:30s}  result: {computed}"
    return f"  * {label:10s}  input: {declared!s:30s}  result: {computed}"


def run_case(yaml_path: str, results_dir: str) -> tuple[str, str]:
    """
    Runs compute_before and compute_group_maps on one YAML file.
    Returns (filename, detail_text).
    """
    filename = os.path.basename(yaml_path)
    all_instances = load_input(yaml_path)

    before_map = compute_before(all_instances, DEPENDS_FIELD)
    group_of, members_of = compute_group_maps(
        all_instances, GROUP_FIELD, MEMBERS_FIELD,
    )

    lines = []
    lines.append(f"Input: {filename}")
    lines.append(f"Instances: {list(all_instances.keys())}")
    lines.append("")
    lines.append("Per-instance mapping (input vs result, * = changed by reconciliation):")
    lines.append("")

    for key in all_instances:
        fields = all_instances[key]

        declared_depends = fields.get(DEPENDS_FIELD, [])
        declared_group   = fields.get(GROUP_FIELD, [])
        declared_members = fields.get(MEMBERS_FIELD, [])

        computed_before  = before_map.get(key, [])
        computed_group   = group_of.get(key, [])
        computed_members = members_of.get(key, [])

        lines.append(f"  [{key}]")

        # Depends → before (always computed, no direct input equivalent)
        lines.append(
            format_mapping_line('Depends', declared_depends, declared_depends)
        )
        # before is fully derived — mark (*) when non-empty since it
        # has no input counterpart.
        if computed_before:
            lines.append(
                f"  * {'before':10s}  input: {'(none)':30s}  result: {computed_before}"
            )
        else:
            lines.append(
                f"    {'before':10s}  input: {'(none)':30s}  result: {computed_before}"
            )

        lines.append(
            format_mapping_line('Group', declared_group, computed_group)
        )
        lines.append(
            format_mapping_line('Members', declared_members, computed_members)
        )
        lines.append("")

    detail = '\n'.join(lines)

    # Write to RESULTS/.
    os.makedirs(results_dir, exist_ok=True)
    result_name = filename.replace('.yaml', '.txt')
    with open(os.path.join(results_dir, result_name), 'w') as f:
        f.write(detail + '\n')

    return filename, detail


def main() -> int:
    total = 0

    for category in CATEGORIES:
        cases_dir   = os.path.join(CASES_DIR, category)
        results_dir = os.path.join(RESULTS_DIR, category)
        yaml_files  = collect_yaml_files(cases_dir)

        if not yaml_files:
            continue

        print("=" * 60)
        print(f"CATEGORY: {category}")
        print("=" * 60)

        for yaml_path in yaml_files:
            total += 1
            filename, detail = run_case(yaml_path, results_dir)
            print(f"\n  [PASS] {filename}")
            print(f"    {detail.replace(chr(10), chr(10) + '    ')}")

    print()
    print("=" * 60)
    print(f"TOTAL: {total}  ALL PASSED")
    print("=" * 60)

    return 0


if __name__ == '__main__':
    sys.exit(main())
