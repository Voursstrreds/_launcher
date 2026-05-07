"""
Input Validation — unit tests for GENERIC_TASK_LAUNCHER.

Feeds every YAML file under TEST_CASES/valid/ and TEST_CASES/invalid/
through Validator.load_and_validate().

Expected behaviour:
    valid/   → load_and_validate returns a non-None dict.
    invalid/ → load_and_validate returns None.

Results are written to both the terminal and the RESULTS/ directory,
mirroring the TEST_CASES/ folder structure.

For valid cases two extra output directories are produced:
    RESULTS/normalised/ — the normalised dict dumped as YAML.
    RESULTS/comparison/ — only fields that changed, raw vs normalised.
"""

import sys
import os
import io
import yaml

# ---------------------------------------------------------------------------
# Path setup — let Python find the LAUNCHER modules.
# ---------------------------------------------------------------------------
LAUNCHER_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), '..', '..', '..', 'Codebase', 'LAUNCHER')
)
sys.path.insert(0, LAUNCHER_DIR)

import Rules
import Validator
Rules.ACTIVE_RULE_SET     = Rules.GENERIC_TASK_LAUNCHER
Validator.ACTIVE_RULE_SET = Rules.GENERIC_TASK_LAUNCHER

from Validator import load_and_validate, load_raw, normalise

# ---------------------------------------------------------------------------
# Directory constants.
# ---------------------------------------------------------------------------
TEST_DIR    = os.path.dirname(os.path.abspath(__file__))
CASES_DIR   = os.path.join(TEST_DIR, 'TEST_CASES')
RESULTS_DIR = os.path.join(TEST_DIR, 'RESULTS')

VALID_CASES_DIR     = os.path.join(CASES_DIR, 'valid')
INVALID_CASES_DIR   = os.path.join(CASES_DIR, 'invalid')
VALID_RESULTS_DIR   = os.path.join(RESULTS_DIR, 'valid')
INVALID_RESULTS_DIR = os.path.join(RESULTS_DIR, 'invalid')
NORMALISED_DIR      = os.path.join(RESULTS_DIR, 'normalised')
COMPARISON_DIR      = os.path.join(RESULTS_DIR, 'comparison')


def collect_yaml_files(directory: str) -> list[str]:
    """Returns sorted list of .yaml files in a directory."""
    files = [
        os.path.join(directory, f)
        for f in os.listdir(directory)
        if f.endswith('.yaml')
    ]
    return sorted(files)


def format_field(key: str, raw_val, norm_val) -> str:
    """
    Formats a single field showing raw → normalised transformation.
    Only shows the arrow when the value actually changed.
    """
    if raw_val == norm_val:
        return f"    {key}: {raw_val!r}"
    return f"    {key}: {raw_val!r}  -->  {norm_val!r}"


def write_normalised(filename: str, normalised_dict: dict) -> None:
    """Dumps the full normalised dict as a YAML file into NORMALISED_DIR."""
    os.makedirs(NORMALISED_DIR, exist_ok=True)
    out_name = filename.replace('.yaml', '_normalised.yaml')
    out_path = os.path.join(NORMALISED_DIR, out_name)
    with open(out_path, 'w') as f:
        yaml.dump(normalised_dict, f, default_flow_style=False, sort_keys=False)


def write_comparison(filename: str, raw_instances: dict, normalised_dict: dict) -> str:
    """
    Writes a comparison file into COMPARISON_DIR showing only the fields
    that normalise() changed.  Returns the comparison text.
    """
    os.makedirs(COMPARISON_DIR, exist_ok=True)
    out_name = filename.replace('.yaml', '_comparison.txt')
    out_path = os.path.join(COMPARISON_DIR, out_name)

    lines = []
    any_change = False

    for inst_key in normalised_dict:
        raw_fields  = raw_instances.get(inst_key, {})
        norm_fields = normalised_dict[inst_key]
        all_keys = list(dict.fromkeys(
            list(raw_fields.keys()) + list(norm_fields.keys())
        ))

        inst_lines = []
        for fkey in all_keys:
            raw_val  = raw_fields.get(fkey)
            norm_val = norm_fields.get(fkey)
            if raw_val != norm_val:
                inst_lines.append(f"    {fkey}:")
                inst_lines.append(f"      raw:        {raw_val!r}")
                inst_lines.append(f"      normalised: {norm_val!r}")

        if inst_lines:
            any_change = True
            lines.append(f"  [{inst_key}]")
            lines.extend(inst_lines)

    if not any_change:
        lines.append("  (no fields changed)")

    comparison_text = '\n'.join(lines)

    with open(out_path, 'w') as f:
        f.write(f"Input: {filename}\n")
        f.write(f"Fields changed by normalise():\n")
        f.write(comparison_text + '\n')

    return comparison_text


def run_case(yaml_path: str, results_dir: str) -> tuple[str, object, str]:
    """
    Runs load_and_validate on a single YAML file.

    Returns (filename, result, detail_text).
    Captures stdout from the validator so error messages are included
    in the result file.

    For valid cases:
      - Writes the normalised dict to RESULTS/normalised/.
      - Writes a field-level comparison to RESULTS/comparison/.
      - Includes both in the detail text.
    """
    filename = os.path.basename(yaml_path)
    result_name = filename.replace('.yaml', '.txt')
    result_path = os.path.join(results_dir, result_name)

    # Load raw before validation so we can compare.
    raw_instances = load_raw(yaml_path)

    # Capture validator stdout (it prints errors there).
    captured = io.StringIO()
    old_stdout = sys.stdout
    sys.stdout = captured

    try:
        result = load_and_validate(yaml_path)
    except Exception as exc:
        result = None
        captured.write(f"EXCEPTION: {exc}\n")
    finally:
        sys.stdout = old_stdout

    validator_output = captured.getvalue()

    # Build the result text.
    lines = []
    lines.append(f"Input: {filename}")
    lines.append(f"Return value: {'dict' if result is not None else 'None'}")

    if result is not None:
        lines.append(f"Instances: {list(result.keys())}")

        # --- Normalised dict dump ---
        write_normalised(filename, result)
        lines.append("")
        lines.append("Normalised output:")
        for inst_key, fields in result.items():
            lines.append(f"  [{inst_key}]")
            for fkey, fval in fields.items():
                lines.append(f"    {fkey}: {fval!r}")

        # --- Comparison: only changed fields ---
        comparison_text = write_comparison(filename, raw_instances, result)
        lines.append("")
        lines.append("Fields changed by normalise():")
        lines.append(comparison_text)

    if validator_output.strip():
        lines.append(f"Validator output:\n{validator_output.rstrip()}")

    detail = '\n'.join(lines)

    # Write to RESULTS/.
    os.makedirs(results_dir, exist_ok=True)
    with open(result_path, 'w') as f:
        f.write(detail + '\n')

    return filename, result, detail


def main() -> int:
    passed  = 0
    failed  = 0
    total   = 0

    # -----------------------------------------------------------------------
    # Valid cases — expect non-None return.
    # -----------------------------------------------------------------------
    print("=" * 60)
    print("VALID CASES (expect: pass validation)")
    print("=" * 60)

    for yaml_path in collect_yaml_files(VALID_CASES_DIR):
        total += 1
        filename, result, detail = run_case(yaml_path, VALID_RESULTS_DIR)

        if result is not None:
            status = "PASS"
            passed += 1
        else:
            status = "FAIL — expected non-None, got None"
            failed += 1

        print(f"\n  [{status}] {filename}")
        print(f"    {detail.replace(chr(10), chr(10) + '    ')}")

    # -----------------------------------------------------------------------
    # Invalid cases — expect None return.
    # -----------------------------------------------------------------------
    print()
    print("=" * 60)
    print("INVALID CASES (expect: fail validation)")
    print("=" * 60)

    for yaml_path in collect_yaml_files(INVALID_CASES_DIR):
        total += 1
        filename, result, detail = run_case(yaml_path, INVALID_RESULTS_DIR)

        if result is None:
            status = "PASS"
            passed += 1
        else:
            status = "FAIL — expected None, got dict"
            failed += 1

        print(f"\n  [{status}] {filename}")
        print(f"    {detail.replace(chr(10), chr(10) + '    ')}")

    # -----------------------------------------------------------------------
    # Summary.
    # -----------------------------------------------------------------------
    print()
    print("=" * 60)
    print(f"TOTAL: {total}  PASSED: {passed}  FAILED: {failed}")
    print("=" * 60)

    return 0 if failed == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
