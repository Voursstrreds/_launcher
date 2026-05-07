"""
Unit File Generation — unit tests for GENERIC_TASK_LAUNCHER.

Tests create_generic_task_launcher_unit_file() in isolation.
Inputs are pre-built GeneratedCommand instances.  The test runner
calls the function, reads the generated unit file, and displays
input (GeneratedCommand fields) next to output (unit file contents).
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
    create_generic_task_launcher_unit_file,
)

# ---------------------------------------------------------------------------
# Directory constants.
# ---------------------------------------------------------------------------
TEST_DIR    = os.path.dirname(os.path.abspath(__file__))
CASES_DIR   = os.path.join(TEST_DIR, 'TEST_CASES')
RESULTS_DIR = os.path.join(TEST_DIR, 'RESULTS')


# ---------------------------------------------------------------------------
# Test case definitions.
# Each case is a dict with:
#   name       : short label
#   instances  : list of GeneratedCommand objects (all instances in this scenario)
#   comment    : what is being tested
# ---------------------------------------------------------------------------

def make_test_cases() -> list[dict]:
    cases = []

    # 01 — Minimal Entry: only ExecStart, no deps/groups
    cases.append({
        'name': '01_minimal_entry',
        'comment': 'Single Entry with no dependencies or groups. Expects [Unit] + [Service] only.',
        'instances': [
            GeneratedCommand(
                key='A', name='a', type_='Entry', path='/usr/bin/a',
                unit_name='a', unit_file_name='a.service',
            ),
        ],
    })

    # 02 — Minimal Group: no Service section
    cases.append({
        'name': '02_minimal_group',
        'comment': 'Single Group with no members. Expects [Unit] only, no [Service].',
        'instances': [
            GeneratedCommand(
                key='G1', name='g1', type_='Group',
                unit_name='g1', unit_file_name='g1.target',
            ),
        ],
    })

    # 03 — Entry with before/after (dependency chain A→B→C)
    cases.append({
        'name': '03_entry_with_deps',
        'comment': 'Chain A→B→C. B has Before=[A] and After=[C]. Tests Before/After/Requires/RequiredBy.',
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
        'comment': 'A and B belong to G1. group field merges into Before. members field merges into After.',
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

    # 05 — Entry with extra args (command_string in ExecStart)
    cases.append({
        'name': '05_entry_with_args',
        'comment': 'Entry with extra args. ExecStart should contain the full command string.',
        'instances': [
            GeneratedCommand(
                key='A', name='a', type_='Entry', path='/usr/bin/a',
                unit_name='a', unit_file_name='a.service',
                extra_args={'Mode': '--mode=fast', 'Verbose': '--verbose'},
            ),
        ],
    })

    # 06 — Nested groups: A,B in G1, G1 in G2
    cases.append({
        'name': '06_nested_groups',
        'comment': 'A,B in G1. G1 in G2. Tests nested group Before/After resolution.',
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
        'comment': 'A depends on B. A and B in G1. Before/After merge deps + groups.',
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

    # 08 — Group with no members but with parent group
    cases.append({
        'name': '08_group_in_group',
        'comment': 'Empty group G1 inside G2. G1 gets Before=g2.target.',
        'instances': [
            GeneratedCommand(
                key='G1', name='g1', type_='Group',
                unit_name='g1', unit_file_name='g1.target',
                group=['G2'],
            ),
            GeneratedCommand(
                key='G2', name='g2', type_='Group',
                unit_name='g2', unit_file_name='g2.target',
                members=['G1'],
            ),
        ],
    })

    # 09 — Large mixed scenario
    cases.append({
        'name': '09_large_mixed',
        'comment': '8 entries, 2 groups, nested. A→B, A→C, B→D, C→D, D→E, F→E. A,B,C in G1. D,E,F in G2. G1 in G2. H,I standalone with args.',
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

    # -----------------------------------------------------------------------
    # Two-axis FailureBehavior / DependencyBehavior matrix.
    #
    # Own axis (child self-crash):
    #   Abort   → emit nothing (systemd default Restart=no).
    #   Restart → emit Restart=on-failure + rate-limit knobs.
    #
    # Dependency axis (parent-edge directives on child):
    #   Ignore  → Requires=<parent>, After=<parent>.
    #   Cascade → Requires=<parent>, After=<parent>, PartOf=<parent>.
    #
    # All four quadrants are exercised below (10-13), followed by a
    # multi-parent case (14) that verifies per-child (not per-edge) emission.
    # -----------------------------------------------------------------------

    # 10 — Abort + Ignore (baseline — no extra directives emitted).
    cases.append({
        'name': '10_abort_ignore',
        'comment': 'Child C has FailureBehavior=Abort, DependencyBehavior=Ignore. No PartOf, no Restart block. Only Requires/After for the parent edge.',
        'instances': [
            GeneratedCommand(
                key='P', name='p', type_='Entry', path='/usr/bin/p',
                unit_name='p', unit_file_name='p.service',
            ),
            GeneratedCommand(
                key='C', name='c', type_='Entry', path='/usr/bin/c',
                unit_name='c', unit_file_name='c.service',
                depends=['P'], after=['P'], before=[],
                failure_behavior='Abort',
                dependency_behavior='Ignore',
            ),
        ],
    })

    # 11 — Abort + Cascade (PartOf emitted on child; no Restart block).
    cases.append({
        'name': '11_abort_cascade',
        'comment': 'Child C has Abort + Cascade. PartOf=p.service emitted on C. No Restart block on C.',
        'instances': [
            GeneratedCommand(
                key='P', name='p', type_='Entry', path='/usr/bin/p',
                unit_name='p', unit_file_name='p.service',
            ),
            GeneratedCommand(
                key='C', name='c', type_='Entry', path='/usr/bin/c',
                unit_name='c', unit_file_name='c.service',
                depends=['P'], after=['P'], before=[],
                failure_behavior='Abort',
                dependency_behavior='Cascade',
            ),
        ],
    })

    # 12 — Restart + Ignore (Restart block on child; no PartOf).
    cases.append({
        'name': '12_restart_ignore',
        'comment': 'Child C has Restart + Ignore. Restart=on-failure + rate-limit knobs emitted on C. No PartOf.',
        'instances': [
            GeneratedCommand(
                key='P', name='p', type_='Entry', path='/usr/bin/p',
                unit_name='p', unit_file_name='p.service',
            ),
            GeneratedCommand(
                key='C', name='c', type_='Entry', path='/usr/bin/c',
                unit_name='c', unit_file_name='c.service',
                depends=['P'], after=['P'], before=[],
                failure_behavior='Restart',
                dependency_behavior='Ignore',
            ),
        ],
    })

    # 13 — Restart + Cascade (both blocks emitted on child).
    cases.append({
        'name': '13_restart_cascade',
        'comment': 'Child C has Restart + Cascade. PartOf=p.service AND Restart=on-failure block both emitted on C.',
        'instances': [
            GeneratedCommand(
                key='P', name='p', type_='Entry', path='/usr/bin/p',
                unit_name='p', unit_file_name='p.service',
            ),
            GeneratedCommand(
                key='C', name='c', type_='Entry', path='/usr/bin/c',
                unit_name='c', unit_file_name='c.service',
                depends=['P', 'Q'], after=['P', 'Q'], before=[],
                failure_behavior='Restart',
                dependency_behavior='Cascade',
            ),
            GeneratedCommand(
                key='Q', name='q', type_='Entry', path='/usr/bin/q',
                unit_name='q', unit_file_name='q.service',
            ),
        ],
    })

    # 14 — Multi-parent Cascade: per-child (not per-edge) emission.
    #   C has two parents P1, P2 — both should appear uniformly in the
    #   child's Requires/After/PartOf lists.
    cases.append({
        'name': '14_cascade_multi_parent',
        'comment': 'Child C with two parents P1, P2. DependencyBehavior=Cascade uniformly covers both edges: PartOf=p1.service p2.service.',
        'instances': [
            GeneratedCommand(
                key='P1', name='p1', type_='Entry', path='/usr/bin/p1',
                unit_name='p1', unit_file_name='p1.service',
            ),
            GeneratedCommand(
                key='P2', name='p2', type_='Entry', path='/usr/bin/p2',
                unit_name='p2', unit_file_name='p2.service',
            ),
            GeneratedCommand(
                key='C', name='c', type_='Entry', path='/usr/bin/c',
                unit_name='c', unit_file_name='c.service',
                depends=['P1', 'P2'], after=['P1', 'P2'], before=[],
                failure_behavior='Restart',
                dependency_behavior='Cascade',
            ),
        ],
    })

    return cases


# ---------------------------------------------------------------------------
# Formatting helpers.
# ---------------------------------------------------------------------------

def format_instance_input(cmd: GeneratedCommand) -> list[str]:
    lines = []
    for f in dataclass_fields(cmd):
        val = getattr(cmd, f.name)
        lines.append(f"    {f.name:20s}  {val!r}")
    lines.append(f"    {'command_string()':20s}  {cmd.command_string()!r}")
    return lines


def read_unit_file(path: str) -> str:
    if not os.path.isfile(path):
        return '(file not found)'
    with open(path) as f:
        return f.read()


# ---------------------------------------------------------------------------
# Runner.
# ---------------------------------------------------------------------------

def run_case(case: dict, results_dir: str) -> tuple[str, str]:
    case_name  = case['name']
    comment    = case['comment']
    instances  = case['instances']

    case_output_dir = os.path.join(CASES_DIR, case_name) + '/'
    os.makedirs(case_output_dir, exist_ok=True)

    for inst in instances:
        create_generic_task_launcher_unit_file(inst, instances, case_output_dir)

    lines = []
    lines.append(f"Test: {case_name}")
    lines.append(f"Comment: {comment}")
    lines.append(f"Instances: {[i.key for i in instances]}")

    for inst in instances:
        lines.append('')
        lines.append(f'  [{inst.key}]')

        lines.append('  Input (GeneratedCommand):')
        lines.extend(format_instance_input(inst))

        unit_path = case_output_dir + inst.unit_file_name
        content = read_unit_file(unit_path)

        lines.append('  Generated unit file:')
        for uf_line in content.rstrip('\n').split('\n'):
            lines.append(f'    | {uf_line}')

        lines.append('  Mapping (input → unit file):')
        before_keys = list(dict.fromkeys(inst.before + inst.group))
        after_keys  = list(dict.fromkeys(inst.after + inst.members))

        key_map = {i.key: i.unit_file_name for i in instances}
        before_resolved = ' '.join(key_map[k] for k in before_keys if k in key_map)
        after_resolved  = ' '.join(key_map[k] for k in after_keys  if k in key_map)

        mapping_rows = [
            ('Description', inst.unit_file_name + ' UNIT FILE'),
            ('Before',      before_resolved if before_resolved else '(empty)'),
            ('After',       after_resolved  if after_resolved  else '(empty)'),
            ('Requires',    after_resolved  if after_resolved  else '(empty)'),
            ('RequiredBy',  before_resolved if before_resolved else '(empty)'),
            ('ExecStart',   inst.command_string() if inst.type_ == 'Entry' else '(none — Group)'),
        ]
        for label, expected in mapping_rows:
            lines.append(f'    {label:14s}  {expected}')

    detail = '\n'.join(lines)

    os.makedirs(results_dir, exist_ok=True)
    result_file = os.path.join(results_dir, case_name + '.txt')
    with open(result_file, 'w') as f:
        f.write(detail + '\n')

    unit_files_dir = os.path.join(results_dir, case_name)
    os.makedirs(unit_files_dir, exist_ok=True)
    for inst in instances:
        src = case_output_dir + inst.unit_file_name
        dst = os.path.join(unit_files_dir, inst.unit_file_name)
        shutil.copy2(src, dst)

    return case_name, detail


def main() -> int:
    cases = make_test_cases()
    total = 0

    print('=' * 60)
    print('UNIT FILE GENERATION — GENERIC_TASK_LAUNCHER')
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
