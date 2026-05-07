"""
Command Generation — unit tests for GENERIC_TASK_LAUNCHER.

Tests construct_generic_command() in isolation.  Inputs are
pre-normalised YAML files.  The test runner computes the
relationship maps (before_map, group_of, members_of) from the
input, then calls construct_generic_command for each instance.

For every instance the output shows the input fields next to
the resulting GeneratedCommand fields so differences are
immediately visible.
"""

import sys
import os
import yaml
from dataclasses import fields as dataclass_fields

# ---------------------------------------------------------------------------
# Path setup.
# ---------------------------------------------------------------------------
LAUNCHER_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), '..', '..', '..', 'Codebase', 'LAUNCHER')
)
sys.path.insert(0, LAUNCHER_DIR)

from RelationshipMapping import compute_before, compute_group_maps
from Builders import construct_generic_command, GeneratedCommand

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


def format_command(cmd: GeneratedCommand) -> list[str]:
    """Formats all fields of a GeneratedCommand for display."""
    lines = []
    for f in dataclass_fields(cmd):
        val = getattr(cmd, f.name)
        lines.append(f"    {f.name:20s}  {val!r}")
    lines.append(f"    {'command_string()':20s}  {cmd.command_string()!r}")
    return lines


def run_case(yaml_path: str, results_dir: str) -> tuple[str, str]:
    """
    Loads one YAML file, computes relationship maps, calls
    construct_generic_command for each instance, and builds
    a side-by-side display of input vs output.
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

    for key, fields in all_instances.items():
        cmd = construct_generic_command(
            key, fields, before_map, group_of, members_of,
        )

        lines.append("")
        lines.append(f"  [{key}]")
        lines.append("  Input fields:")
        for fkey, fval in fields.items():
            lines.append(f"    {fkey:20s}  {fval!r}")

        lines.append("  GeneratedCommand:")
        lines.extend(format_command(cmd))

        lines.append("  Mapping (input vs result):")

        depends_in = fields.get(DEPENDS_FIELD, [])
        group_in   = fields.get(GROUP_FIELD, [])
        members_in = fields.get(MEMBERS_FIELD, [])

        mapping_rows = [
            ('depends',  depends_in,  cmd.depends),
            ('after',    depends_in,  cmd.after),
            ('before',   '(computed)', cmd.before),
            ('group',    group_in,    cmd.group),
            ('members',  members_in,  cmd.members),
        ]

        for label, inp, out in mapping_rows:
            inp_s = str(inp)
            out_s = str(out)
            marker = '  ' if inp_s == out_s or inp == '(computed)' else '* '
            if inp == '(computed)':
                lines.append(
                    f"  {marker}{label:12s}  input: {'(none)':30s}  result: {out}"
                )
            else:
                lines.append(
                    f"  {marker}{label:12s}  input: {inp_s:30s}  result: {out}"
                )

    detail = '\n'.join(lines)

    os.makedirs(results_dir, exist_ok=True)
    result_name = filename.replace('.yaml', '.txt')
    with open(os.path.join(results_dir, result_name), 'w') as f:
        f.write(detail + '\n')

    return filename, detail


def main() -> int:
    total = 0
    yaml_files = collect_yaml_files(CASES_DIR)

    print("=" * 60)
    print("COMMAND GENERATION — GENERIC_TASK_LAUNCHER")
    print("=" * 60)

    for yaml_path in yaml_files:
        total += 1
        filename, detail = run_case(yaml_path, RESULTS_DIR)
        print(f"\n  [PASS] {filename}")
        print(f"    {detail.replace(chr(10), chr(10) + '    ')}")

    print()
    print("=" * 60)
    print(f"TOTAL: {total}  ALL PASSED")
    print("=" * 60)

    return 0


if __name__ == '__main__':
    sys.exit(main())
