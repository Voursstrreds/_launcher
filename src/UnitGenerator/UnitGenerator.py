import sys
from Validator         import load_and_validate
from CommandGenerator import build_all
from UnitFileCreator import generate_all
from ManifestWriter   import write_manifest
from Rules         import ACTIVE_RULE_SET, GENERIC_TASK_LAUNCHER, PODMAN_COMPOSE
from Builders      import (
    set_generic_task_launcher_failure_behavior_default,
    set_generic_task_launcher_dependency_behavior_default,
    set_podman_compose_failure_behavior_default,
    set_podman_compose_dependency_behavior_default,
)

# ---------------------------------------------------------------------------
# Configuration — default values used when no argument is provided.
# ---------------------------------------------------------------------------
INPUT_FILE                  = './src/input.yaml'
UNIT_FILE_OUTPUT_PATH       = './Results/unit_files/'
MANIFEST_FILE_PATH          = './Results/tmp/manifest.ini'
FAILURE_BEHAVIOR_DEFAULT    = 'Abort'
DEPENDENCY_BEHAVIOR_DEFAULT = 'Ignore'


def parse_args() -> None:
    """
    Reads command-line arguments and maps them to the global configuration
    variables. Arguments are passed as FLAG=VALUE pairs with no leading
    dashes, matching the variable names exactly.

    Recognised flags:
        INPUT_FILE
        UNIT_FILE_OUTPUT_PATH
        MANIFEST_FILE_PATH
        FAILURE_BEHAVIOR_DEFAULT
        DEPENDENCY_BEHAVIOR_DEFAULT

    Unrecognised flags are silently ignored. Variables for which no
    argument is provided retain their default values.
    """
    global INPUT_FILE, UNIT_FILE_OUTPUT_PATH, MANIFEST_FILE_PATH
    global FAILURE_BEHAVIOR_DEFAULT, DEPENDENCY_BEHAVIOR_DEFAULT

    for arg in sys.argv[1:]:
        if '=' not in arg:
            continue
        flag, _, value = arg.partition('=')
        if   flag == 'INPUT_FILE':                  INPUT_FILE                  = value
        elif flag == 'UNIT_FILE_OUTPUT_PATH':       UNIT_FILE_OUTPUT_PATH       = value
        elif flag == 'MANIFEST_FILE_PATH':          MANIFEST_FILE_PATH          = value
        elif flag == 'FAILURE_BEHAVIOR_DEFAULT':    FAILURE_BEHAVIOR_DEFAULT    = value
        elif flag == 'DEPENDENCY_BEHAVIOR_DEFAULT': DEPENDENCY_BEHAVIOR_DEFAULT = value


def _print_instance(instance) -> None:
    """
    Prints the fields of one command instance. Branches on ACTIVE_RULE_SET
    to display the fields appropriate to the active rule-set.
    """
    print()
    print(f"  [{instance.key}]")

    if ACTIVE_RULE_SET is GENERIC_TASK_LAUNCHER:
        print(f"    Name             : {instance.name}")
        print(f"    Type             : {instance.type_}")
        print(f"    Path             : {instance.path}")
        print(f"    Unit name        : {instance.unit_name}")
        print(f"    Unit file name   : {instance.unit_file_name}")
        print(f"    Depends          : {instance.depends}")
        print(f"    After            : {instance.after}")
        print(f"    Before           : {instance.before}")
        print(f"    Group            : {instance.group}")
        print(f"    Members          : {instance.members}")
        print(f"    Extra args       : {instance.extra_args}")
        print(f"    Order            : {instance.order}")
        print(f"    FailureBehavior  : {instance.failure_behavior}")
        print(f"    DependencyBehavior: {instance.dependency_behavior}")
        print(f"    Command          : {instance.command_string()}")

    elif ACTIVE_RULE_SET is PODMAN_COMPOSE:
        print(f"    Name              : {instance.name}")
        print(f"    Type              : {instance.type_}")
        print(f"    Unit file name    : {instance.unit_file_name}")
        print(f"    Image             : {instance.image}")
        print(f"    Container name    : {instance.container_name}")
        print(f"    Depends           : {instance.depends}")
        print(f"    Networks          : {instance.networks}")
        print(f"    Network members   : {instance.network_members}")
        print(f"    Ports             : {instance.ports}")
        print(f"    Volumes           : {instance.volumes}")
        print(f"    Environment       : {instance.environment}")
        print(f"    Command           : {instance.command}")
        print(f"    Entrypoint        : {instance.entrypoint}")
        print(f"    Working dir       : {instance.working_dir}")
        print(f"    After             : {instance.after}")
        print(f"    Before            : {instance.before}")
        print(f"    Group             : {instance.group}")
        print(f"    FailureBehavior   : {instance.failure_behavior}")
        print(f"    DependencyBehavior: {instance.dependency_behavior}")
        print(f"    ExecStart         : {instance.exec_start}")
        print(f"    ExecStop          : {instance.exec_stop}")
        print(f"    ExecStopPost      : {instance.exec_stop_post}")


def run() -> None:
    """
    Main entry point. Executes all four pipeline steps in sequence using
    the configuration variables resolved by parse_args().
    """

    # Push the resolved FailureBehaviorDefault / DependencyBehaviorDefault
    # into Builders before any GeneratedCommand is constructed, so they
    # become the fallbacks for instances whose YAML has no such field.
    # Both rule-sets carry their own module-level defaults — dispatch by
    # active schema so the right ones are updated.
    if ACTIVE_RULE_SET is GENERIC_TASK_LAUNCHER:
        set_generic_task_launcher_failure_behavior_default(FAILURE_BEHAVIOR_DEFAULT)
        set_generic_task_launcher_dependency_behavior_default(DEPENDENCY_BEHAVIOR_DEFAULT)
    elif ACTIVE_RULE_SET is PODMAN_COMPOSE:
        set_podman_compose_failure_behavior_default(FAILURE_BEHAVIOR_DEFAULT)
        set_podman_compose_dependency_behavior_default(DEPENDENCY_BEHAVIOR_DEFAULT)

    # -----------------------------------------------------------------------
    # Step 1 — Validation
    # -----------------------------------------------------------------------
    print("=" * 60)
    print("VALIDATION")
    print("=" * 60)

    validated_input = load_and_validate(INPUT_FILE)

    if not validated_input:
        raise SystemExit("Validation failed. Aborting.")

    print("Validation passed.")
    print(f"Instances found: {list(validated_input.keys())}")

    # -----------------------------------------------------------------------
    # Step 2 — Command generation
    # -----------------------------------------------------------------------
    print()
    print("=" * 60)
    print("COMMAND GENERATION")
    print("=" * 60)

    instances = build_all(validated_input)

    for instance in instances:
        _print_instance(instance)

    # -----------------------------------------------------------------------
    # Step 3 — Unit file generation
    # -----------------------------------------------------------------------
    print()
    print("=" * 60)
    print("UNIT FILE GENERATION")
    print("=" * 60)

    generate_all(instances, ACTIVE_RULE_SET, output_path=UNIT_FILE_OUTPUT_PATH)

    for instance in instances:
        print(f"  Written: {UNIT_FILE_OUTPUT_PATH}{instance.unit_file_name}")

    # -----------------------------------------------------------------------
    # Step 4 — Manifest file generation
    # -----------------------------------------------------------------------
    print()
    print("=" * 60)
    print("MANIFEST GENERATION")
    print("=" * 60)

    write_manifest(instances, ACTIVE_RULE_SET, MANIFEST_FILE_PATH)

    print(f"  Written: {MANIFEST_FILE_PATH}")

    print()
    print("=" * 60)
    print(f"Done. {len(instances)} instance(s) processed.")
    print("=" * 60)


if __name__ == '__main__':
    parse_args()
    run()
