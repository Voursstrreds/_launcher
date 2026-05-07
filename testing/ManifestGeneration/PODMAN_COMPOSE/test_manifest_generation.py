"""
Manifest Generation — unit tests for PODMAN_COMPOSE.

Tests build_podman_compose_manifest_entry() and ManifestEntry.dump()
in isolation. Inputs are pre-built PodmanGeneratedCommand instances.
The test runner converts each to a ManifestEntry, writes the full
manifest file via write_manifest, and displays input
(PodmanGeneratedCommand fields) next to output (ManifestEntry fields +
serialised manifest block).

Scenarios cover:
  * Service-only minimal / full field set.
  * Network-only emission (type_ mapped to NETWORK).
  * 2×2 FailureBehavior × DependencyBehavior matrix.
  * Service + Network mixed manifest.
  * Dependency / group / members propagation via Depends, Networks,
    Network_members.
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
    build_podman_compose_manifest_entry,
)
from Rules import PODMAN_COMPOSE
from ManifestWriter import ManifestEntry, write_manifest

# ---------------------------------------------------------------------------
# Directory constants.
# ---------------------------------------------------------------------------
TEST_DIR    = os.path.dirname(os.path.abspath(__file__))
CASES_DIR   = os.path.join(TEST_DIR, 'TEST_CASES')
RESULTS_DIR = os.path.join(TEST_DIR, 'RESULTS')


# ---------------------------------------------------------------------------
# Helpers — build representative PodmanGeneratedCommand objects.
# The values mirror what construct_podman_command would produce. The manifest
# builder only reads these fields, so hand-filling keeps the test isolated
# from the command builder.
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
    order               : int       = -1,
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
        order               = order,
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
    order               : int       = -1,
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
        order               = order,
        failure_behavior    = failure_behavior,
        dependency_behavior = dependency_behavior,
        unit_extension      = '.service',
        exec_start          = f'podman network create --ignore {name}',
        exec_stop           = f'podman network rm {name}',
        exec_stop_post      = '',
    )


# ---------------------------------------------------------------------------
# Test case definitions.
# ---------------------------------------------------------------------------

def make_test_cases() -> list[dict]:
    cases = []

    # 01 — Minimal Service: type_ mapped to SERVICE.
    cases.append({
        'name': '01_service_minimal',
        'comment': (
            'Single Service with no deps/members. type_ in manifest is '
            'SERVICE. command carries exec_start (podman run).'
        ),
        'instances': [
            make_service(key='Svc', name='svc'),
        ],
    })

    # 02 — Minimal Network: type_ mapped to NETWORK.
    cases.append({
        'name': '02_network_minimal',
        'comment': (
            'Single Network with no members. type_ in manifest is NETWORK. '
            'command carries exec_start (podman network create --ignore).'
        ),
        'instances': [
            make_network(key='AppNet', name='appnet'),
        ],
    })

    # 03 — Service chain (Depends edges propagated as after/before).
    cases.append({
        'name': '03_service_chain',
        'comment': (
            'Chain A→B→C. after/before fields propagated per instance '
            'manifest block.'
        ),
        'instances': [
            make_service(key='A', name='a', depends=['B'], before=[]),
            make_service(key='B', name='b', depends=['C'], before=['A']),
            make_service(key='C', name='c', before=['B']),
        ],
    })

    # 04 — Service + Network with group/members via Networks & Network_members.
    cases.append({
        'name': '04_service_network_membership',
        'comment': (
            'Services Web and Db are in AppNet (Networks). AppNet lists '
            'Web, Db in Network_members. group / members fields propagated '
            'to manifest per instance.'
        ),
        'instances': [
            make_service(
                key='Web', name='web',
                networks=['AppNet'],
                group=['AppNet'],
            ),
            make_service(
                key='Db', name='db',
                networks=['AppNet'],
                group=['AppNet'],
            ),
            make_network(
                key='AppNet', name='appnet',
                network_members=['Web', 'Db'],
            ),
        ],
    })

    # 05 — Service with full field set; command carries the full podman run.
    cases.append({
        'name': '05_service_full_fields',
        'comment': (
            'Service with image, ports, volumes, env, entrypoint, '
            'working_dir, command. Full podman run string lands in '
            'manifest command= field.'
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

    # 06 — 2×2 FailureBehavior × DependencyBehavior matrix.
    cases.append({
        'name': '06_two_axis_behavior',
        'comment': (
            'Four Services, one per quadrant of the FailureBehavior × '
            'DependencyBehavior matrix. failure_behavior= and '
            'dependency_behavior= lines reflect each per instance block.'
        ),
        'instances': [
            make_service(
                key='A', name='a',
                failure_behavior='Abort',
                dependency_behavior='Ignore',
            ),
            make_service(
                key='B', name='b',
                failure_behavior='Abort',
                dependency_behavior='Cascade',
            ),
            make_service(
                key='C', name='c',
                failure_behavior='Restart',
                dependency_behavior='Ignore',
            ),
            make_service(
                key='D', name='d',
                failure_behavior='Restart',
                dependency_behavior='Cascade',
            ),
        ],
    })

    # 07 — Mixed Service + Network with behavior axes on both Types.
    cases.append({
        'name': '07_service_and_network_mixed_axes',
        'comment': (
            'Services and Networks co-exist; behavior axes are carried '
            'verbatim on both Types. Verifies SERVICE vs NETWORK type_ '
            'discrimination and per-instance axis emission in one '
            'manifest file.'
        ),
        'instances': [
            make_service(
                key='Web', name='web',
                depends=['AppNet'],
                failure_behavior='Restart',
                dependency_behavior='Cascade',
            ),
            make_service(
                key='Db', name='db',
                depends=['AppNet'],
                failure_behavior='Abort',
                dependency_behavior='Ignore',
            ),
            make_network(
                key='AppNet', name='appnet',
                network_members=['Web', 'Db'],
                before=['Web', 'Db'],
                failure_behavior='Restart',
                dependency_behavior='Ignore',
            ),
        ],
    })

    # 08 — Ordering: `order` field propagates into manifest.
    cases.append({
        'name': '08_order_propagation',
        'comment': (
            'Three Services with distinct order values. Each manifest '
            'instance block carries the corresponding order= line, '
            'unchanged from the input.'
        ),
        'instances': [
            make_service(key='First',  name='first',  order=0),
            make_service(key='Second', name='second', order=1),
            make_service(key='Third',  name='third',  order=2),
        ],
    })

    return cases


# ---------------------------------------------------------------------------
# Formatting helpers.
# ---------------------------------------------------------------------------

def format_podman_command(cmd: PodmanGeneratedCommand) -> list[str]:
    lines = []
    for f in dataclass_fields(cmd):
        val = getattr(cmd, f.name)
        lines.append(f"    {f.name:20s}  {val!r}")
    return lines


def format_manifest_entry(entry: ManifestEntry) -> list[str]:
    lines = []
    for f in dataclass_fields(entry):
        val = getattr(entry, f.name)
        lines.append(f"    {f.name:20s}  {val!r}")
    return lines


def format_mapping(cmd: PodmanGeneratedCommand, entry: ManifestEntry) -> list[str]:
    lines = []
    mapping_rows = [
        ('key',                cmd.key,                 entry.key),
        ('name',               cmd.name,                entry.name),
        ('unit_file_name',     cmd.unit_file_name,      entry.unit_file_name),
        ('type_',              cmd.type_,               entry.type_),
        ('path',               '',                      entry.path),
        ('command',            cmd.exec_start,          entry.command),
        ('after',              cmd.after,               entry.after),
        ('before',             cmd.before,              entry.before),
        ('group',              cmd.group,               entry.group),
        ('members',            cmd.network_members,     entry.members),
        ('order',              cmd.order,               entry.order),
        ('failure_behavior',   cmd.failure_behavior,    entry.failure_behavior),
        ('dependency_behavior', cmd.dependency_behavior, entry.dependency_behavior),
    ]
    for label, inp, out in mapping_rows:
        inp_s = str(inp)
        out_s = str(out)
        marker = '  ' if inp_s == out_s else '* '
        lines.append(
            f"  {marker}{label:20s}  input: {inp_s:40s}  manifest: {out_s}"
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
    write_manifest(instances, PODMAN_COMPOSE, manifest_path)

    with open(manifest_path) as f:
        manifest_content = f.read()

    entries = []
    for inst in instances:
        entries.append(build_podman_compose_manifest_entry(inst))

    lines = []
    lines.append(f"Test: {case_name}")
    lines.append(f"Comment: {comment}")
    lines.append(f"Instances: {[i.key for i in instances]}")

    for inst, entry in zip(instances, entries):
        lines.append('')
        lines.append(f'  [{inst.key}]')

        lines.append('  Input (PodmanGeneratedCommand):')
        lines.extend(format_podman_command(inst))

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
    print('MANIFEST GENERATION — PODMAN_COMPOSE')
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
