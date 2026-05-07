"""
Parser Integration Test — GENERIC_TASK_LAUNCHER full parser pipeline.

Runs the complete chain for each YAML input:
    3a. Validation + normalisation
    3b. Relationship mapping
    3c. DAG validation (dep, group, unified)
    3d. Command generation
    3e. Unit file generation
    3f. Manifest generation

Valid cases produce a full report with intermediate results at each
stage.  Invalid cases report which stage failed and the error message.
Unit files and manifest.ini are dumped separately per case.
"""

import sys
import os
import io
import shutil
import yaml
from dataclasses import fields as dataclass_fields

# ---------------------------------------------------------------------------
# Path setup.
# ---------------------------------------------------------------------------
LAUNCHER_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), '..', '..', '..', 'Codebase', 'LAUNCHER')
)
sys.path.insert(0, LAUNCHER_DIR)

from Validator import load_raw, normalise, validate_types, validate_references
from RelationshipMapping import (
    compute_before, compute_group_maps, validate_dag,
    compute_unified_before, validate_unified_dag,
)
from Builders import (
    GeneratedCommand,
    construct_generic_command,
    create_generic_task_launcher_unit_file,
    build_generic_task_launcher_manifest_entry,
)
from Rules import GENERIC_TASK_LAUNCHER
from ManifestWriter import ManifestEntry, write_manifest

# ---------------------------------------------------------------------------
# Constants.
# ---------------------------------------------------------------------------
DEPENDS_FIELD = 'Depends'
GROUP_FIELD   = 'Group'
MEMBERS_FIELD = 'Members'

TEST_DIR    = os.path.dirname(os.path.abspath(__file__))
CASES_DIR   = os.path.join(TEST_DIR, 'TEST_CASES')
RESULTS_DIR = os.path.join(TEST_DIR, 'RESULTS')


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------

def collect_yaml_files(directory: str) -> list[str]:
    if not os.path.isdir(directory):
        return []
    return sorted(
        os.path.join(directory, f)
        for f in os.listdir(directory)
        if f.endswith('.yaml')
    )


def format_dict(d: dict, indent: int = 4) -> list[str]:
    prefix = ' ' * indent
    lines = []
    for k, v in d.items():
        lines.append(f"{prefix}{k:20s}  {v!r}")
    return lines


def format_command(cmd: GeneratedCommand, indent: int = 6) -> list[str]:
    prefix = ' ' * indent
    lines = []
    for f in dataclass_fields(cmd):
        val = getattr(cmd, f.name)
        lines.append(f"{prefix}{f.name:20s}  {val!r}")
    lines.append(f"{prefix}{'command_string()':20s}  {cmd.command_string()!r}")
    return lines


def format_manifest_entry(entry: ManifestEntry, indent: int = 6) -> list[str]:
    prefix = ' ' * indent
    lines = []
    for f in dataclass_fields(entry):
        val = getattr(entry, f.name)
        lines.append(f"{prefix}{f.name:20s}  {val!r}")
    return lines


def read_file(path: str) -> str:
    if not os.path.isfile(path):
        return '(file not found)'
    with open(path) as f:
        return f.read()


# ---------------------------------------------------------------------------
# Pipeline stages.
# ---------------------------------------------------------------------------

class PipelineError(Exception):
    def __init__(self, stage: str, message: str):
        self.stage = stage
        self.message = message
        super().__init__(f"[{stage}] {message}")


def stage_validate(yaml_path: str) -> tuple[dict, dict]:
    raw = load_raw(yaml_path)
    if raw is None:
        raise PipelineError('3a. VALIDATION', 'YAML load failed')

    type_errors = validate_types(raw)
    if type_errors:
        msg_lines = []
        for inst, errs in type_errors.items():
            msg_lines.append(f"  [{inst}]: {errs}")
        raise PipelineError('3a. VALIDATION (type)', '\n'.join(msg_lines))

    ref_errors = validate_references(raw)
    if ref_errors:
        msg_lines = []
        for inst, errs in ref_errors.items():
            for e in errs:
                msg_lines.append(f"  [{inst}]: {e}")
        raise PipelineError('3a. VALIDATION (reference)', '\n'.join(msg_lines))

    normalised = {name: normalise(data) for name, data in raw.items()}
    return raw, normalised


def stage_relationship_mapping(normalised: dict) -> tuple[dict, dict, dict]:
    before_map = compute_before(normalised, DEPENDS_FIELD)
    group_of, members_of = compute_group_maps(normalised, GROUP_FIELD, MEMBERS_FIELD)
    return before_map, group_of, members_of


def stage_dag_validation(normalised: dict, before_map: dict, group_of: dict) -> dict:
    try:
        validate_dag(normalised, DEPENDS_FIELD, GROUP_FIELD, MEMBERS_FIELD)
    except SystemExit as e:
        raise PipelineError('3c. DAG VALIDATION (dep/group)', str(e))

    unified_before = compute_unified_before(before_map, group_of)

    try:
        validate_unified_dag(unified_before)
    except SystemExit as e:
        raise PipelineError('3c. DAG VALIDATION (unified)', str(e))

    return unified_before


def stage_command_generation(
    normalised: dict, before_map: dict, group_of: dict, members_of: dict,
) -> list[GeneratedCommand]:
    commands = []
    for key, fields in normalised.items():
        cmd = construct_generic_command(key, fields, before_map, group_of, members_of)
        commands.append(cmd)
    return commands


def stage_unit_file_generation(
    commands: list[GeneratedCommand], output_dir: str,
) -> None:
    os.makedirs(output_dir, exist_ok=True)
    if not output_dir.endswith('/'):
        output_dir += '/'
    for cmd in commands:
        create_generic_task_launcher_unit_file(cmd, commands, output_dir)


def stage_manifest_generation(
    commands: list[GeneratedCommand], manifest_path: str,
) -> list[ManifestEntry]:
    write_manifest(commands, GENERIC_TASK_LAUNCHER, manifest_path)
    entries = []
    for cmd in commands:
        entries.append(build_generic_task_launcher_manifest_entry(cmd))
    return entries


# ---------------------------------------------------------------------------
# Report builders.
# ---------------------------------------------------------------------------

def build_valid_report(
    filename: str,
    raw: dict,
    normalised: dict,
    before_map: dict,
    group_of: dict,
    members_of: dict,
    unified_before: dict,
    commands: list[GeneratedCommand],
    entries: list[ManifestEntry],
    unit_dir: str,
    manifest_path: str,
) -> str:
    lines = []
    lines.append(f"Input: {filename}")
    lines.append(f"Instances: {list(raw.keys())}")
    lines.append(f"Result: VALID — full pipeline completed")
    lines.append('')

    # --- Stage 3a: Validation + Normalisation ---
    lines.append('=' * 50)
    lines.append('  STAGE 3a: VALIDATION + NORMALISATION')
    lines.append('=' * 50)

    for key in raw:
        lines.append(f'  [{key}]')
        lines.append('    Raw input:')
        lines.extend(format_dict(raw[key]))
        lines.append('    Normalised:')
        lines.extend(format_dict(normalised[key]))

        changed = []
        for field_name in normalised[key]:
            raw_val = raw[key].get(field_name)
            norm_val = normalised[key][field_name]
            if raw_val != norm_val:
                changed.append(f"      {field_name:20s}  {raw_val!r:30s} → {norm_val!r}")
        if changed:
            lines.append('    Changes:')
            lines.extend(changed)
        lines.append('')

    # --- Stage 3b: Relationship Mapping ---
    lines.append('=' * 50)
    lines.append('  STAGE 3b: RELATIONSHIP MAPPING')
    lines.append('=' * 50)

    lines.append('    before_map:')
    for k, v in before_map.items():
        lines.append(f"      {k:20s}  {v}")
    lines.append('    group_of:')
    for k, v in group_of.items():
        lines.append(f"      {k:20s}  {v}")
    lines.append('    members_of:')
    for k, v in members_of.items():
        lines.append(f"      {k:20s}  {v}")
    lines.append('')

    # --- Stage 3c: DAG Validation ---
    lines.append('=' * 50)
    lines.append('  STAGE 3c: DAG VALIDATION')
    lines.append('=' * 50)
    lines.append('    Dependency DAG:    PASS')
    lines.append('    Group DAG:         PASS')
    lines.append('    unified_before:')
    for k, v in unified_before.items():
        lines.append(f"      {k:20s}  {v}")
    lines.append('    Unified DAG:       PASS')
    lines.append('')

    # --- Stage 3d: Command Generation ---
    lines.append('=' * 50)
    lines.append('  STAGE 3d: COMMAND GENERATION')
    lines.append('=' * 50)

    for cmd in commands:
        lines.append(f'  [{cmd.key}]')
        lines.extend(format_command(cmd))
        lines.append('')

    # --- Stage 3e: Unit File Generation ---
    lines.append('=' * 50)
    lines.append('  STAGE 3e: UNIT FILE GENERATION')
    lines.append('=' * 50)

    for cmd in commands:
        uf_path = os.path.join(unit_dir, cmd.unit_file_name)
        content = read_file(uf_path)
        lines.append(f'  [{cmd.key}] {cmd.unit_file_name}')
        for uf_line in content.rstrip('\n').split('\n'):
            lines.append(f'    | {uf_line}')
        lines.append('')

    # --- Stage 3f: Manifest Generation ---
    lines.append('=' * 50)
    lines.append('  STAGE 3f: MANIFEST GENERATION')
    lines.append('=' * 50)

    for cmd, entry in zip(commands, entries):
        lines.append(f'  [{cmd.key}]')
        lines.extend(format_manifest_entry(entry))
        lines.append('')

    manifest_content = read_file(manifest_path)
    lines.append('  Full manifest:')
    for ml in manifest_content.rstrip('\n').split('\n'):
        lines.append(f'    | {ml}')

    return '\n'.join(lines)


def build_invalid_report(filename: str, raw: dict, error: PipelineError) -> str:
    lines = []
    lines.append(f"Input: {filename}")
    if raw:
        lines.append(f"Instances: {list(raw.keys())}")
    lines.append(f"Result: INVALID — pipeline aborted")
    lines.append(f"Failed at: {error.stage}")
    lines.append(f"Error:")
    for err_line in error.message.split('\n'):
        lines.append(f"  {err_line}")
    return '\n'.join(lines)


# ---------------------------------------------------------------------------
# Runner.
# ---------------------------------------------------------------------------

def run_valid_case(yaml_path: str, results_dir: str) -> tuple[str, str, bool]:
    filename = os.path.basename(yaml_path)
    case_name = filename.replace('.yaml', '')

    case_result_dir = os.path.join(results_dir, case_name)
    os.makedirs(case_result_dir, exist_ok=True)

    unit_dir = os.path.join(case_result_dir, 'unit_files')
    os.makedirs(unit_dir, exist_ok=True)
    manifest_path = os.path.join(case_result_dir, 'manifest.ini')

    try:
        raw, normalised = stage_validate(yaml_path)
        before_map, group_of, members_of = stage_relationship_mapping(normalised)
        unified_before = stage_dag_validation(normalised, before_map, group_of)
        commands = stage_command_generation(normalised, before_map, group_of, members_of)
        stage_unit_file_generation(commands, unit_dir)
        entries = stage_manifest_generation(commands, manifest_path)

        detail = build_valid_report(
            filename, raw, normalised,
            before_map, group_of, members_of, unified_before,
            commands, entries, unit_dir, manifest_path,
        )
        passed = True

    except PipelineError as e:
        raw_fallback = load_raw(yaml_path)
        detail = build_invalid_report(filename, raw_fallback, e)
        passed = False

    result_file = os.path.join(results_dir, case_name + '.txt')
    with open(result_file, 'w') as f:
        f.write(detail + '\n')

    return filename, detail, passed


def run_invalid_case(yaml_path: str, results_dir: str) -> tuple[str, str, bool]:
    filename = os.path.basename(yaml_path)
    case_name = filename.replace('.yaml', '')

    raw = load_raw(yaml_path)

    try:
        _, normalised = stage_validate(yaml_path)
        before_map, group_of, members_of = stage_relationship_mapping(normalised)
        unified_before = stage_dag_validation(normalised, before_map, group_of)
        commands = stage_command_generation(normalised, before_map, group_of, members_of)

        detail = f"Input: {filename}\n"
        detail += f"Instances: {list(raw.keys())}\n"
        detail += "Result: UNEXPECTED PASS — expected failure but pipeline succeeded"
        passed = False

    except PipelineError as e:
        detail = build_invalid_report(filename, raw, e)
        passed = True

    result_file = os.path.join(results_dir, case_name + '.txt')
    os.makedirs(results_dir, exist_ok=True)
    with open(result_file, 'w') as f:
        f.write(detail + '\n')

    return filename, detail, passed


# ---------------------------------------------------------------------------
# Main.
# ---------------------------------------------------------------------------

def main() -> int:
    valid_dir   = os.path.join(CASES_DIR, 'valid')
    invalid_dir = os.path.join(CASES_DIR, 'invalid')
    os.makedirs(RESULTS_DIR, exist_ok=True)

    valid_files   = collect_yaml_files(valid_dir)
    invalid_files = collect_yaml_files(invalid_dir)

    total = 0
    passed = 0
    failed = 0

    print('=' * 60)
    print('PARSER INTEGRATION TEST — GENERIC_TASK_LAUNCHER (3a → 3f)')
    print('=' * 60)

    # --- Valid cases ---
    print('\n  VALID CASES (expect full pipeline success):')
    print('  ' + '-' * 56)

    for yaml_path in valid_files:
        total += 1
        filename, detail, ok = run_valid_case(yaml_path, RESULTS_DIR)
        tag = 'PASS' if ok else 'FAIL'
        if ok:
            passed += 1
        else:
            failed += 1
        print(f'\n  [{tag}] {filename}')
        print(f'    {detail.replace(chr(10), chr(10) + "    ")}')

    # --- Invalid cases ---
    print('\n  INVALID CASES (expect pipeline abort):')
    print('  ' + '-' * 56)

    for yaml_path in invalid_files:
        total += 1
        filename, detail, ok = run_invalid_case(yaml_path, RESULTS_DIR)
        tag = 'PASS' if ok else 'FAIL'
        if ok:
            passed += 1
        else:
            failed += 1
        print(f'\n  [{tag}] {filename}')
        print(f'    {detail.replace(chr(10), chr(10) + "    ")}')

    # --- Summary ---
    print()
    print('=' * 60)
    status = 'ALL PASSED' if failed == 0 else f'{failed} FAILED'
    print(f'TOTAL: {total}  PASSED: {passed}  FAILED: {failed}  {status}')
    print('=' * 60)

    return 0 if failed == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
