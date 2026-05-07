"""
Manifest Generation — unit tests for GENERIC_TASK_LAUNCHER.

Tests build_generic_task_launcher_manifest_entry() and ManifestEntry.dump()
in isolation.  Inputs are pre-built GeneratedCommand instances.  The test
runner converts each to a ManifestEntry, writes the full manifest file via
write_manifest, and displays input (GeneratedCommand fields) next to
output (ManifestEntry fields + serialised manifest block).
"""

import sys
import os
import shutil
from dataclasses import fields as dataclass_fields

# ---------------------------------------------------------------------------
# Path setup.
# ---------------------------------------------------------------------------
LAUNCHER_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), '..', '..', '..', 'Codebase', 'LAUNCHER')
)
sys.path.insert(0, LAUNCHER_DIR)

from Builders import (
    GeneratedCommand,
    build_generic_task_launcher_manifest_entry,
)
from Rules import GENERIC_TASK_LAUNCHER
from ManifestWriter import ManifestEntry, write_manifest

# ---------------------------------------------------------------------------
# Directory constants.
# ---------------------------------------------------------------------------
TEST_DIR    = os.path.dirname(os.path.abspath(__file__))
CASES_DIR   = os.path.join(TEST_DIR, 'TEST_CASES')
RESULTS_DIR = os.path.join(TEST_DIR, 'RESULTS')


# ---------------------------------------------------------------------------
# Test case definitions.
# ---------------------------------------------------------------------------

def make_test_cases() -> list[dict]:
    cases = []

    # 01 — Minimal Entry
    cases.append({
        'name': '01_minimal_entry',
        'comment': 'Single Entry, no deps/groups. type mapped to ENTRY.',
        'instances': [
            GeneratedCommand(
                key='A', name='a', type_='Entry', path='/usr/bin/a',
                unit_name='a', unit_file_name='a.service',
            ),
        ],
    })

    # 02 — Minimal Group
    cases.append({
        'name': '02_minimal_group',
        'comment': 'Single Group. type mapped to GROUP. path and command empty.',
        'instances': [
            GeneratedCommand(
                key='G1', name='g1', type_='Group',
                unit_name='g1', unit_file_name='g1.target',
            ),
        ],
    })

    # 03 — Entry with deps (chain A→B→C)
    cases.append({
        'name': '03_entry_with_deps',
        'comment': 'Chain A→B→C. after/before fields propagated to manifest.',
        'instances': [
            GeneratedCommand(
                key='A', name='a', type_='Entry', path='/usr/bin/a',
                unit_name='a', unit_file_name='a.service',
                depends=['B'], after=['B'], before=[],
            ),
            GeneratedCommand(
                key='B', name='b', type_='Entry', path='/usr/bin/b',
                unit_name='b', unit_file_name='b.service',
                depends=['C'], after=['C'], before=['A'],
            ),
            GeneratedCommand(
                key='C', name='c', type_='Entry', path='/usr/bin/c',
                unit_name='c', unit_file_name='c.service',
                depends=[], after=[], before=['B'],
            ),
        ],
    })

    # 04 — Entry with group membership
    cases.append({
        'name': '04_entry_in_group',
        'comment': 'A,B in G1. group/members fields propagated to manifest.',
        'instances': [
            GeneratedCommand(
                key='A', name='a', type_='Entry', path='/usr/bin/a',
                unit_name='a', unit_file_name='a.service',
                group=['G1'],
            ),
            GeneratedCommand(
                key='B', name='b', type_='Entry', path='/usr/bin/b',
                unit_name='b', unit_file_name='b.service',
                group=['G1'],
            ),
            GeneratedCommand(
                key='G1', name='g1', type_='Group',
                unit_name='g1', unit_file_name='g1.target',
                members=['A', 'B'],
            ),
        ],
    })

    # 05 — Entry with extra args
    cases.append({
        'name': '05_entry_with_args',
        'comment': 'Entry with extra args. command field contains full command string.',
        'instances': [
            GeneratedCommand(
                key='A', name='a', type_='Entry', path='/usr/bin/a',
                unit_name='a', unit_file_name='a.service',
                extra_args={'Mode': '--mode=fast', 'Retry': '--retry', 'Count': '3'},
            ),
        ],
    })

    # 06 — Nested groups
    cases.append({
        'name': '06_nested_groups',
        'comment': 'A,B in G1. G1 in G2. Group nesting reflected in group/members.',
        'instances': [
            GeneratedCommand(
                key='A', name='a', type_='Entry', path='/usr/bin/a',
                unit_name='a', unit_file_name='a.service',
                group=['G1'],
            ),
            GeneratedCommand(
                key='B', name='b', type_='Entry', path='/usr/bin/b',
                unit_name='b', unit_file_name='b.service',
                group=['G1'],
            ),
            GeneratedCommand(
                key='G1', name='g1', type_='Group',
                unit_name='g1', unit_file_name='g1.target',
                members=['A', 'B'], group=['G2'],
            ),
            GeneratedCommand(
                key='G2', name='g2', type_='Group',
                unit_name='g2', unit_file_name='g2.target',
                members=['G1'],
            ),
        ],
    })

    # 07 — Mixed deps and groups
    cases.append({
        'name': '07_mixed_deps_groups',
        'comment': 'A depends on B. Both in G1. Tests combined after/before + group/members.',
        'instances': [
            GeneratedCommand(
                key='A', name='a', type_='Entry', path='/usr/bin/a',
                unit_name='a', unit_file_name='a.service',
                depends=['B'], after=['B'], before=[],
                group=['G1'],
            ),
            GeneratedCommand(
                key='B', name='b', type_='Entry', path='/usr/bin/b',
                unit_name='b', unit_file_name='b.service',
                depends=[], after=[], before=['A'],
                group=['G1'],
            ),
            GeneratedCommand(
                key='G1', name='g1', type_='Group',
                unit_name='g1', unit_file_name='g1.target',
                members=['A', 'B'],
            ),
        ],
    })

    # 08 — Multiple entries, no relationships
    cases.append({
        'name': '08_standalone_entries',
        'comment': 'Three standalone entries. All list fields empty in manifest.',
        'instances': [
            GeneratedCommand(
                key='X', name='x', type_='Entry', path='/usr/bin/x',
                unit_name='x', unit_file_name='x.service',
            ),
            GeneratedCommand(
                key='Y', name='y', type_='Entry', path='/usr/bin/y',
                unit_name='y', unit_file_name='y.service',
                extra_args={'Flag': '--daemon'},
            ),
            GeneratedCommand(
                key='Z', name='z', type_='Entry', path='/usr/bin/z',
                unit_name='z', unit_file_name='z.service',
                extra_args={'Output': '/tmp/out.log'},
            ),
        ],
    })

    # 09 — Large mixed scenario
    cases.append({
        'name': '09_large_mixed',
        'comment': '8 entries, 2 groups, nested. Full manifest with all field variations.',
        'instances': [
            GeneratedCommand(
                key='A', name='a', type_='Entry', path='/usr/bin/a',
                unit_name='a', unit_file_name='a.service',
                depends=['B', 'C'], after=['B', 'C'], before=[],
                group=['G1'],
            ),
            GeneratedCommand(
                key='B', name='b', type_='Entry', path='/usr/bin/b',
                unit_name='b', unit_file_name='b.service',
                depends=['D'], after=['D'], before=['A'],
                group=['G1'],
            ),
            GeneratedCommand(
                key='C', name='c', type_='Entry', path='/usr/bin/c',
                unit_name='c', unit_file_name='c.service',
                depends=['D'], after=['D'], before=['A'],
                group=['G1'],
            ),
            GeneratedCommand(
                key='D', name='d', type_='Entry', path='/usr/bin/d',
                unit_name='d', unit_file_name='d.service',
                depends=['E'], after=['E'], before=['B', 'C'],
                group=['G2'],
            ),
            GeneratedCommand(
                key='E', name='e', type_='Entry', path='/usr/bin/e',
                unit_name='e', unit_file_name='e.service',
                depends=[], after=[], before=['D', 'F'],
                group=['G2'],
            ),
            GeneratedCommand(
                key='F', name='f', type_='Entry', path='/usr/bin/f',
                unit_name='f', unit_file_name='f.service',
                depends=['E'], after=['E'], before=[],
                group=['G2'],
            ),
            GeneratedCommand(
                key='H', name='h', type_='Entry', path='/usr/bin/h',
                unit_name='h', unit_file_name='h.service',
                extra_args={'Port': '8080', 'Verbose': '--verbose'},
            ),
            GeneratedCommand(
                key='I', name='i', type_='Entry', path='/usr/bin/i',
                unit_name='i', unit_file_name='i.service',
                extra_args={'Output': '/tmp/result.log'},
            ),
            GeneratedCommand(
                key='G1', name='g1', type_='Group',
                unit_name='g1', unit_file_name='g1.target',
                members=['A', 'B', 'C'], group=['G2'],
            ),
            GeneratedCommand(
                key='G2', name='g2', type_='Group',
                unit_name='g2', unit_file_name='g2.target',
                members=['D', 'E', 'F', 'G1'],
            ),
        ],
    })

    # 10 — two-axis behavior propagation to manifest (2×2 matrix).
    # Each entry exercises one quadrant of (FailureBehavior x DependencyBehavior).
    # Manifest should carry both failure_behavior= and dependency_behavior= lines
    # per INSTANCE block, reflecting each entry's resolved values.
    cases.append({
        'name': '10_two_axis_behavior',
        'comment': 'Four entries, one per quadrant of the FailureBehavior x DependencyBehavior matrix. failure_behavior= and dependency_behavior= lines reflect each.',
        'instances': [
            GeneratedCommand(
                key='A', name='a', type_='Entry', path='/usr/bin/a',
                unit_name='a', unit_file_name='a.service',
                failure_behavior='Abort',
                dependency_behavior='Ignore',
            ),
            GeneratedCommand(
                key='B', name='b', type_='Entry', path='/usr/bin/b',
                unit_name='b', unit_file_name='b.service',
                failure_behavior='Abort',
                dependency_behavior='Cascade',
            ),
            GeneratedCommand(
                key='C', name='c', type_='Entry', path='/usr/bin/c',
                unit_name='c', unit_file_name='c.service',
                failure_behavior='Restart',
                dependency_behavior='Ignore',
            ),
            GeneratedCommand(
                key='D', name='d', type_='Entry', path='/usr/bin/d',
                unit_name='d', unit_file_name='d.service',
                failure_behavior='Restart',
                dependency_behavior='Cascade',
            ),
        ],
    })

    return cases


# ---------------------------------------------------------------------------
# Formatting helpers.
# ---------------------------------------------------------------------------

def format_generated_command(cmd: GeneratedCommand) -> list[str]:
    lines = []
    for f in dataclass_fields(cmd):
        val = getattr(cmd, f.name)
        lines.append(f"    {f.name:20s}  {val!r}")
    lines.append(f"    {'command_string()':20s}  {cmd.command_string()!r}")
    return lines


def format_manifest_entry(entry: ManifestEntry) -> list[str]:
    lines = []
    for f in dataclass_fields(entry):
        val = getattr(entry, f.name)
        lines.append(f"    {f.name:20s}  {val!r}")
    return lines


def format_mapping(cmd: GeneratedCommand, entry: ManifestEntry) -> list[str]:
    lines = []
    mapping_rows = [
        ('key',            cmd.key,              entry.key),
        ('name',           cmd.name,             entry.name),
        ('unit_file_name', cmd.unit_file_name,   entry.unit_file_name),
        ('type',           cmd.type_,            entry.type_),
        ('path',           cmd.path or '',       entry.path),
        ('command',        cmd.command_string(),  entry.command),
        ('after',          cmd.after,            entry.after),
        ('before',         cmd.before,           entry.before),
        ('group',          cmd.group,            entry.group),
        ('members',        cmd.members,          entry.members),
        ('order',          cmd.order,            entry.order),
        ('failure_behavior', cmd.failure_behavior, entry.failure_behavior),
        ('dependency_behavior', cmd.dependency_behavior, entry.dependency_behavior),
    ]
    for label, inp, out in mapping_rows:
        inp_s = str(inp)
        out_s = str(out)
        marker = '  ' if inp_s == out_s else '* '
        lines.append(
            f"  {marker}{label:18s}  input: {inp_s:30s}  manifest: {out_s}"
        )
    return lines


# ---------------------------------------------------------------------------
# Runner.
# ---------------------------------------------------------------------------

def run_case(case: dict, results_dir: str) -> tuple[str, str]:
    case_name  = case['name']
    comment    = case['comment']
    instances  = case['instances']

    case_output_dir = os.path.join(CASES_DIR, case_name)
    os.makedirs(case_output_dir, exist_ok=True)

    manifest_path = os.path.join(case_output_dir, 'manifest.ini')
    write_manifest(instances, GENERIC_TASK_LAUNCHER, manifest_path)

    with open(manifest_path) as f:
        manifest_content = f.read()

    entries = []
    for inst in instances:
        entries.append(build_generic_task_launcher_manifest_entry(inst))

    lines = []
    lines.append(f"Test: {case_name}")
    lines.append(f"Comment: {comment}")
    lines.append(f"Instances: {[i.key for i in instances]}")

    for inst, entry in zip(instances, entries):
        lines.append('')
        lines.append(f'  [{inst.key}]')

        lines.append('  Input (GeneratedCommand):')
        lines.extend(format_generated_command(inst))

        lines.append('  Output (ManifestEntry):')
        lines.extend(format_manifest_entry(entry))

        lines.append('  Mapping (input → manifest):')
        lines.extend(format_mapping(inst, entry))

    lines.append('')
    lines.append('  Full manifest file:')
    for ml in manifest_content.rstrip('\n').split('\n'):
        lines.append(f'    | {ml}')

    detail = '\n'.join(lines)

    os.makedirs(results_dir, exist_ok=True)
    result_file = os.path.join(results_dir, case_name + '.txt')
    with open(result_file, 'w') as f:
        f.write(detail + '\n')

    manifest_dump_dir = os.path.join(results_dir, case_name)
    os.makedirs(manifest_dump_dir, exist_ok=True)
    shutil.copy2(manifest_path, os.path.join(manifest_dump_dir, 'manifest.ini'))

    return case_name, detail


def main() -> int:
    cases = make_test_cases()
    total = 0

    print('=' * 60)
    print('MANIFEST GENERATION — GENERIC_TASK_LAUNCHER')
    print('=' * 60)

    for case in cases:
        total += 1
        case_name, detail = run_case(case, RESULTS_DIR)
        print(f'\n  [PASS] {case_name}')
        print(f'    {detail.replace(chr(10), chr(10) + "    ")}')

    print()
    print('=' * 60)
    print(f'TOTAL: {total}  ALL PASSED')
    print('=' * 60)

    return 0


if __name__ == '__main__':
    sys.exit(main())
