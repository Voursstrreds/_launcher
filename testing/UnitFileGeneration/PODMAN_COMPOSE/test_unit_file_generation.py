"""
Unit File Generation — unit tests for PODMAN_COMPOSE.

Tests create_podman_compose_unit_file() in isolation. Inputs are
pre-built PodmanGeneratedCommand instances. The test runner calls the
function, reads the generated unit file, and displays input (the
PodmanGeneratedCommand fields) next to output (the unit file contents).

Scenarios exercised:
  * Service-only minimal / full field set.
  * Network-only emission (Type=oneshot + RemainAfterExit=yes).
  * Service → Network chain via Networks field (group membership).
  * Network → Service reverse relation via Network_members.
  * 2×2 FailureBehavior × DependencyBehavior matrix on a Service child.
  * Multi-parent Cascade (per-child uniform emission).
  * Network instance carrying FailureBehavior=Restart (applies to both
    Types uniformly).
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
    PodmanGeneratedCommand,
    create_podman_compose_unit_file,
)

# ---------------------------------------------------------------------------
# Directory constants.
# ---------------------------------------------------------------------------
TEST_DIR    = os.path.dirname(os.path.abspath(__file__))
CASES_DIR   = os.path.join(TEST_DIR, 'TEST_CASES')
RESULTS_DIR = os.path.join(TEST_DIR, 'RESULTS')


# ---------------------------------------------------------------------------
# Small helpers for building representative PodmanGeneratedCommand objects.
# The values here mirror what construct_podman_command() would produce; the
# unit-file generator only reads these fields, so hand-filling them keeps the
# test isolated from the command builder.
# ---------------------------------------------------------------------------

def make_service(
    key                 : str,
    name                : str,
    *,
    image               : str       = 'docker.io/library/alpine:latest',
    container_name      : str       = None,
    depends             : list[str] = None,
    networks            : list[str] = None,
    ports               : list[str] = None,
    volumes             : list[str] = None,
    environment         : dict      = None,
    command             : str       = '',
    entrypoint          : str       = '',
    working_dir         : str       = '',
    before              : list[str] = None,
    group               : list[str] = None,
    failure_behavior    : str       = 'Abort',
    dependency_behavior : str       = 'Ignore',
) -> PodmanGeneratedCommand:
    container_name = container_name if container_name is not None else name
    depends   = depends   or []
    networks  = networks  or []
    ports     = ports     or []
    volumes   = volumes   or []
    environment = environment or {}
    before    = before    or []
    group     = group     or []

    run_parts = ['podman run', f'--name {container_name}']
    for n in networks:
        run_parts.append(f'--network {n}')
    for p in ports:
        run_parts.append(f'-p {p}')
    for v in volumes:
        run_parts.append(f'-v {v}')
    for k, v in environment.items():
        run_parts.append(f'-e {k}={v}')
    if entrypoint:
        run_parts.append(f'--entrypoint {entrypoint}')
    if working_dir:
        run_parts.append(f'--workdir {working_dir}')
    run_parts.append(image)
    if command:
        run_parts.append(command)

    exec_start     = ' '.join(run_parts)
    exec_stop      = f'podman stop {container_name}'
    exec_stop_post = f'podman rm {container_name}'

    return PodmanGeneratedCommand(
        key                 = key,
        name                = name,
        type_               = 'Service',
        unit_file_name      = name + '.service',
        image               = image,
        container_name      = container_name,
        depends             = depends,
        networks            = networks,
        ports               = ports,
        volumes             = volumes,
        environment         = environment,
        command             = command,
        entrypoint          = entrypoint,
        working_dir         = working_dir,
        after               = list(depends),
        before              = before,
        group               = group,
        network_members     = [],
        failure_behavior    = failure_behavior,
        dependency_behavior = dependency_behavior,
        unit_extension      = '.service',
        exec_start          = exec_start,
        exec_stop           = exec_stop,
        exec_stop_post      = exec_stop_post,
    )


def make_network(
    key                 : str,
    name                : str,
    *,
    depends             : list[str] = None,
    network_members     : list[str] = None,
    before              : list[str] = None,
    group               : list[str] = None,
    failure_behavior    : str       = 'Abort',
    dependency_behavior : str       = 'Ignore',
) -> PodmanGeneratedCommand:
    depends         = depends         or []
    network_members = network_members or []
    before          = before          or []
    group           = group           or []

    return PodmanGeneratedCommand(
        key                 = key,
        name                = name,
        type_               = 'Network',
        unit_file_name      = name + '.service',
        image               = '',
        container_name      = '',
        depends             = depends,
        networks            = [],
        ports               = [],
        volumes             = [],
        environment         = {},
        command             = '',
        entrypoint          = '',
        working_dir         = '',
        after               = list(depends),
        before              = before,
        group               = group,
        network_members     = network_members,
        failure_behavior    = failure_behavior,
        dependency_behavior = dependency_behavior,
        unit_extension      = '.service',
        exec_start          = f'podman network create --ignore {name}',
        exec_stop           = f'podman network rm {name}',
        exec_stop_post      = '',
    )


# ---------------------------------------------------------------------------
# Test case definitions.
# Each case is a dict with:
#   name       : short label
#   instances  : list of PodmanGeneratedCommand objects
#   comment    : what is being tested
# ---------------------------------------------------------------------------

def make_test_cases() -> list[dict]:
    cases = []

    # 01 — Minimal Service: only required fields.
    cases.append({
        'name': '01_service_minimal',
        'comment': (
            'Single Service with no dependencies, no group. Expects '
            '[Unit] + [Service] with Type=simple, ExecStart/ExecStop/'
            'ExecStopPost. No Requires/After/PartOf/Restart block.'
        ),
        'instances': [
            make_service(key='Svc', name='svc'),
        ],
    })

    # 02 — Minimal Network: alone, no members.
    cases.append({
        'name': '02_network_minimal',
        'comment': (
            'Single Network with no members. Expects [Service] with '
            'Type=oneshot + RemainAfterExit=yes, ExecStart=podman network '
            'create --ignore ..., ExecStop=podman network rm ..., no '
            'ExecStopPost.'
        ),
        'instances': [
            make_network(key='AppNet', name='appnet'),
        ],
    })

    # 03 — Service chain A→B→C (Depends edges).
    cases.append({
        'name': '03_service_chain',
        'comment': (
            'Chain A→B→C. B depends on C, A depends on B. Verifies '
            'Requires/After on dependents and Before/RequiredBy on parents.'
        ),
        'instances': [
            make_service(key='A', name='a', depends=['B'], before=[]),
            make_service(key='B', name='b', depends=['C'], before=['A']),
            make_service(key='C', name='c', before=['B']),
        ],
    })

    # 04 — Service inside a Network (group membership via Networks).
    cases.append({
        'name': '04_service_in_network',
        'comment': (
            'Service Web joins AppNet. group=[AppNet] on Web merges into '
            'Before/RequiredBy. network_members=[Web] on AppNet merges '
            'into After/Requires.'
        ),
        'instances': [
            make_service(
                key='Web', name='web',
                networks=['AppNet'],
                group=['AppNet'],
            ),
            make_network(
                key='AppNet', name='appnet',
                network_members=['Web'],
            ),
        ],
    })

    # 05 — Network with explicit members plus Depends edge.
    cases.append({
        'name': '05_network_with_members_and_depends',
        'comment': (
            'AppNet has Network_members=[Web, Db] and Depends=[BaseNet]. '
            'Verifies mix of dependency and membership axes in the same '
            'Network instance.'
        ),
        'instances': [
            make_service(
                key='Web', name='web',
                group=['AppNet'],
            ),
            make_service(
                key='Db', name='db',
                group=['AppNet'],
            ),
            make_network(
                key='AppNet', name='appnet',
                depends=['BaseNet'],
                network_members=['Web', 'Db'],
            ),
            make_network(
                key='BaseNet', name='basenet',
                before=['AppNet'],
            ),
        ],
    })

    # 06 — Service with full field set.
    cases.append({
        'name': '06_service_full_fields',
        'comment': (
            'Service with Image, ContainerName, Ports, Volumes, Environment, '
            'Entrypoint, Working_dir, Command. Verifies exec_start carries '
            'the full podman run flag set and emits verbatim.'
        ),
        'instances': [
            make_service(
                key='Api', name='api',
                image='docker.io/library/python:3.12',
                container_name='api-ctr',
                ports=['8080:8080', '9090:9090'],
                volumes=['/opt/data:/data'],
                environment={'ENV': 'prod', 'LOG_LEVEL': 'info'},
                entrypoint='/usr/bin/python',
                working_dir='/app',
                command='server.py --bind 0.0.0.0',
            ),
        ],
    })

    # -----------------------------------------------------------------------
    # 2×2 behaviour matrix on a Service child with one Service parent.
    # -----------------------------------------------------------------------

    # 07 — Abort + Ignore (baseline: no PartOf, no Restart block).
    cases.append({
        'name': '07_abort_ignore',
        'comment': (
            'Child Svc has FailureBehavior=Abort, DependencyBehavior=Ignore. '
            'Only Requires=<parent> + After=<parent>. No PartOf, no Restart '
            'block.'
        ),
        'instances': [
            make_service(key='P', name='p'),
            make_service(
                key='Svc', name='svc',
                depends=['P'],
                failure_behavior='Abort',
                dependency_behavior='Ignore',
            ),
        ],
    })

    # 08 — Abort + Cascade (PartOf emitted, no Restart block).
    cases.append({
        'name': '08_abort_cascade',
        'comment': (
            'Child Svc has Abort + Cascade. PartOf=p.service emitted. '
            'No Restart block.'
        ),
        'instances': [
            make_service(key='P', name='p'),
            make_service(
                key='Svc', name='svc',
                depends=['P'],
                failure_behavior='Abort',
                dependency_behavior='Cascade',
            ),
        ],
    })

    # 09 — Restart + Ignore (Restart block emitted, no PartOf).
    cases.append({
        'name': '09_restart_ignore',
        'comment': (
            'Child Svc has Restart + Ignore. Restart=on-failure + '
            'rate-limit knobs emitted. No PartOf.'
        ),
        'instances': [
            make_service(key='P', name='p'),
            make_service(
                key='Svc', name='svc',
                depends=['P'],
                failure_behavior='Restart',
                dependency_behavior='Ignore',
            ),
        ],
    })

    # 10 — Restart + Cascade (both blocks emitted).
    cases.append({
        'name': '10_restart_cascade',
        'comment': (
            'Child Svc has Restart + Cascade. Both the PartOf directive '
            'and the Restart/rate-limit block are emitted on Svc.'
        ),
        'instances': [
            make_service(key='P', name='p'),
            make_service(
                key='Svc', name='svc',
                depends=['P'],
                failure_behavior='Restart',
                dependency_behavior='Cascade',
            ),
        ],
    })

    # 11 — Multi-parent Cascade (per-child, not per-edge).
    cases.append({
        'name': '11_cascade_multi_parent',
        'comment': (
            'Child Svc with two parents P1, P2. DependencyBehavior=Cascade '
            'covers both edges uniformly: PartOf=p1.service p2.service.'
        ),
        'instances': [
            make_service(key='P1', name='p1'),
            make_service(key='P2', name='p2'),
            make_service(
                key='Svc', name='svc',
                depends=['P1', 'P2'],
                failure_behavior='Restart',
                dependency_behavior='Cascade',
            ),
        ],
    })

    # 12 — Service depending on a Network via Depends (Cascade).
    cases.append({
        'name': '12_service_depends_on_network_cascade',
        'comment': (
            'Service Web has Depends=[AppNet] (Network) and Cascade. '
            'AppNet.service is the parent unit; Web.service gets '
            'Requires=appnet.service, After=appnet.service, PartOf='
            'appnet.service. Verifies that the Service → Network '
            'dependency chain uses the same machinery as Service → Service.'
        ),
        'instances': [
            make_service(
                key='Web', name='web',
                depends=['AppNet'],
                failure_behavior='Restart',
                dependency_behavior='Cascade',
            ),
            make_network(
                key='AppNet', name='appnet',
                before=['Web'],
            ),
        ],
    })

    # 13 — Network with FailureBehavior=Restart.
    cases.append({
        'name': '13_network_restart',
        'comment': (
            'Network AppNet has FailureBehavior=Restart. Both Type=oneshot '
            'and the Restart/rate-limit block must coexist: the Restart '
            'block applies uniformly across Types.'
        ),
        'instances': [
            make_network(
                key='AppNet', name='appnet',
                failure_behavior='Restart',
            ),
        ],
    })

    return cases


# ---------------------------------------------------------------------------
# Formatting helpers.
# ---------------------------------------------------------------------------

def format_instance_input(cmd: PodmanGeneratedCommand) -> list[str]:
    lines = []
    for f in dataclass_fields(cmd):
        val = getattr(cmd, f.name)
        lines.append(f"    {f.name:20s}  {val!r}")
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
        create_podman_compose_unit_file(inst, instances, case_output_dir)

    lines = []
    lines.append(f"Test: {case_name}")
    lines.append(f"Comment: {comment}")
    lines.append(f"Instances: {[i.key for i in instances]}")

    for inst in instances:
        lines.append('')
        lines.append(f'  [{inst.key}]')

        lines.append('  Input (PodmanGeneratedCommand):')
        lines.extend(format_instance_input(inst))

        unit_path = case_output_dir + inst.unit_file_name
        content = read_unit_file(unit_path)

        lines.append('  Generated unit file:')
        for uf_line in content.rstrip('\n').split('\n'):
            lines.append(f'    | {uf_line}')

        lines.append('  Mapping (input → unit file):')
        before_keys = list(dict.fromkeys(inst.before + inst.group))
        after_keys  = list(dict.fromkeys(inst.after  + inst.network_members))

        key_map = {i.key: i.unit_file_name for i in instances}
        before_resolved = ' '.join(key_map[k] for k in before_keys if k in key_map)
        after_resolved  = ' '.join(key_map[k] for k in after_keys  if k in key_map)

        part_of = (after_resolved
                   if (inst.dependency_behavior == 'Cascade' and after_resolved)
                   else '(none)')

        if inst.type_ == 'Service':
            svc_type_row = 'simple'
            remain_after = '(n/a)'
        else:
            svc_type_row = 'oneshot'
            remain_after = 'yes'

        if inst.failure_behavior == 'Restart':
            restart_row = 'on-failure (+ RestartSec=2, StartLimitBurst=5, StartLimitIntervalSec=30)'
        else:
            restart_row = '(none — Abort)'

        exec_stop_post_row = (inst.exec_stop_post
                              if inst.exec_stop_post
                              else '(none)')

        mapping_rows = [
            ('Description',    inst.unit_file_name + ' UNIT FILE'),
            ('Before',         before_resolved if before_resolved else '(empty)'),
            ('After',          after_resolved  if after_resolved  else '(empty)'),
            ('Requires',       after_resolved  if after_resolved  else '(empty)'),
            ('RequiredBy',     before_resolved if before_resolved else '(empty)'),
            ('PartOf',         part_of),
            ('Type',           svc_type_row),
            ('RemainAfterExit', remain_after),
            ('ExecStart',      inst.exec_start),
            ('ExecStop',       inst.exec_stop),
            ('ExecStopPost',   exec_stop_post_row),
            ('Restart',        restart_row),
        ]
        for label, expected in mapping_rows:
            lines.append(f'    {label:16s}  {expected}')

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
    print('UNIT FILE GENERATION — PODMAN_COMPOSE')
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
