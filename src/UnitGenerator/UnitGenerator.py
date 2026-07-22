import os
import sys
import time
from Validator import load_and_validate
from Rules     import ACTIVE_RULE_SET


# Time origin for TIMING|py|... emissions. Captured at module import (i.e.
# after the C parent's fork+exec), so py-side offsets are relative to the
# Python interpreter start, not the C session start. Step 4's C-side row is
# the umbrella that contains all py rows.
_T0_NS = time.monotonic_ns()


def _emit_timing(idx: str, step_name: str, t0_ns: int, t1_ns: int, rc: int = 0) -> None:
    t0_us      = (t0_ns - _T0_NS) // 1000
    t1_us      = (t1_ns - _T0_NS) // 1000
    elapsed_us = (t1_ns - t0_ns)  // 1000
    print(f"TIMING|py|{idx}|{step_name}|{t0_us}|{t1_us}|{elapsed_us}|{rc}", flush=True)


# ---------------------------------------------------------------------------
# Configuration — default values used when no argument is provided.
# Mirrors the old-source defaults; Manager.c will eventually pass these via
# the command line, but until that integration lands we run UnitGenerator.py
# directly and either accept the defaults or override per-arg.
# ---------------------------------------------------------------------------
INPUT_FILE                  = './src/input.yaml'
UNIT_FILE_OUTPUT_PATH       = './Results/unit_files/'
MANIFEST_FILE_PATH          = './Results/tmp/manifest.ini'
FAILURE_BEHAVIOR_DEFAULT    = 'Abort'
MAPPING_BEHAVIOR_DEFAULT    = 'Ignore'


def parse_args() -> None:
    """
    Reads command-line arguments and maps them to the module-level
    configuration variables above. Arguments are passed as FLAG=VALUE
    pairs with no leading dashes, matching the variable names exactly.

    Recognised flags:
        INPUT_FILE
        UNIT_FILE_OUTPUT_PATH
        MANIFEST_FILE_PATH
        FAILURE_BEHAVIOR_DEFAULT
        MAPPING_BEHAVIOR_DEFAULT

    Unrecognised flags are silently ignored. Variables for which no
    argument is provided retain their default values.
    """
    global INPUT_FILE, UNIT_FILE_OUTPUT_PATH, MANIFEST_FILE_PATH
    global FAILURE_BEHAVIOR_DEFAULT, MAPPING_BEHAVIOR_DEFAULT

    for arg in sys.argv[1:]:
        if '=' not in arg:
            continue
        flag, _, value = arg.partition('=')
        if   flag == 'INPUT_FILE':               INPUT_FILE               = value
        elif flag == 'UNIT_FILE_OUTPUT_PATH':    UNIT_FILE_OUTPUT_PATH    = value
        elif flag == 'MANIFEST_FILE_PATH':       MANIFEST_FILE_PATH       = value
        elif flag == 'FAILURE_BEHAVIOR_DEFAULT': FAILURE_BEHAVIOR_DEFAULT = value
        elif flag == 'MAPPING_BEHAVIOR_DEFAULT': MAPPING_BEHAVIOR_DEFAULT = value


def run() -> None:
    """
    Pipeline entry. Consumes the active RuleSet's fields top to bottom —
    the field declaration order in Rules.py IS the running order:
    schema (validation), blueprint_builder (construction + reconciliation
    + DAG validation), unit_file_builder (unit-file emission),
    manifest_builder (manifest emission).
    """

    # -----------------------------------------------------------------------
    # Step P1 — Validation
    # -----------------------------------------------------------------------
    print("=" * 60)
    print("VALIDATION")
    print("=" * 60)

    t0 = time.monotonic_ns()
    validated_input = load_and_validate(INPUT_FILE, ACTIVE_RULE_SET)
    t1 = time.monotonic_ns()
    _emit_timing("p1", "validation", t0, t1, 0 if validated_input is not None else -1)

    if validated_input is None:
        raise SystemExit("Validation failed. Aborting.")

    print("Validation passed.")
    print(f"Sections found: {list(validated_input.keys())}")

    # -----------------------------------------------------------------------
    # Steps P2-P6 — the RuleSet's builder fields, called in declaration
    # order (blueprint_builder → unit_file_builder → manifest_builder).
    # Each step emits its own TIMING|py row (p2_4, p5, p6), replacing the
    # old single p2_6 orchestrator umbrella.
    # -----------------------------------------------------------------------
    print()
    print("=" * 60)
    print("BUILD + EMIT")
    print("=" * 60)

    t0 = time.monotonic_ns()
    blueprints = ACTIVE_RULE_SET.blueprint_builder(
        validated_input,
        FAILURE_BEHAVIOR_DEFAULT,
        MAPPING_BEHAVIOR_DEFAULT,
    )
    t1 = time.monotonic_ns()
    _emit_timing("p2_4", "blueprint_builder", t0, t1, 0)

    t0 = time.monotonic_ns()
    ACTIVE_RULE_SET.unit_file_builder(blueprints, UNIT_FILE_OUTPUT_PATH)
    t1 = time.monotonic_ns()
    _emit_timing("p5", "unit_file_builder", t0, t1, 0)

    t0 = time.monotonic_ns()
    ACTIVE_RULE_SET.manifest_builder(blueprints, MANIFEST_FILE_PATH)
    t1 = time.monotonic_ns()
    _emit_timing("p6", "manifest_builder", t0, t1, 0)

    print(f"Instances processed: {len(blueprints)}")
    for bp in blueprints:
        print(f"  Written: {os.path.join(UNIT_FILE_OUTPUT_PATH, bp.unit_file_name)}")
    print(f"  Manifest: {MANIFEST_FILE_PATH}")

    # Final total line for the python side.
    t_total = time.monotonic_ns()
    _emit_timing("0", "total", _T0_NS, t_total, 0)


if __name__ == '__main__':
    parse_args()
    run()
