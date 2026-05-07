from __future__ import annotations
from dataclasses import dataclass
from typing      import Callable
from ManifestWriter import ManifestEntry
from Schemas         import (
    _ENTRY_SCHEMA,
    _GROUP_SCHEMA,
    _GENERIC_TASK_LAUNCHER_ENTRY_TYPE,
    _GENERIC_TASK_LAUNCHER_GROUP_TYPE,
    _PODMAN_COMPOSE_SERVICE_SCHEMA,
    _PODMAN_COMPOSE_NETWORK_SCHEMA,
    _PODMAN_COMPOSE_SERVICE_TYPE,
    _PODMAN_COMPOSE_NETWORK_TYPE,
)
from Builders import (
    build_generic_task_launcher_command,
    create_generic_task_launcher_unit_file,
    build_generic_task_launcher_manifest_entry,
    build_podman_compose_command,
    create_podman_compose_unit_file,
    build_podman_compose_manifest_entry,
)


@dataclass
class RuleSetDescriptor:
    """
    Bundles the syntactic side (schemas) with the semantic side (builders)
    for one launcher rule-set. Validator.py, CommandGenerator.py,
    UnitFileCreator.py, and ManifestWriter.py all dispatch through a
    descriptor instance and stay rule-set-agnostic.

    Fields:
        discriminator         : the field name whose value selects the sub-schema.
        list_fields           : field names split from space-separated strings.
        ref_fields            : field names whose elements are cross-references.
        schemas               : maps discriminator values to Cerberus dicts
                                (sourced from Schemas.py).
        command_builder       : callable(key, fields, all_instances) -> any
                                (sourced from Builders.py).
        unit_file_builder     : callable(instance, all_instances, output_path) -> None
                                (sourced from Builders.py).
        manifest_builder      : callable(instance) -> ManifestEntry
                                (sourced from Builders.py). ManifestWriter.py
                                calls this without knowledge of the command type.
        allow_unknown_fields  : when True, undeclared fields are accepted as strings.
    """
    discriminator        : str
    list_fields          : set
    ref_fields           : set
    schemas              : dict
    command_builder      : Callable[[str, dict, dict], object]
    unit_file_builder    : Callable[[object, list, str], None]
    manifest_builder     : Callable[[object], ManifestEntry]
    allow_unknown_fields : bool = False


GENERIC_TASK_LAUNCHER = RuleSetDescriptor(
    discriminator        = 'Type',
    list_fields          = {'Depends', 'Group', 'Members'},
    ref_fields           = {'Depends', 'Group', 'Members'},
    allow_unknown_fields = True,
    command_builder      = build_generic_task_launcher_command,
    unit_file_builder    = create_generic_task_launcher_unit_file,
    manifest_builder     = build_generic_task_launcher_manifest_entry,
    schemas              = {
        _GENERIC_TASK_LAUNCHER_ENTRY_TYPE : _ENTRY_SCHEMA,
        _GENERIC_TASK_LAUNCHER_GROUP_TYPE : _GROUP_SCHEMA,
    },
)


PODMAN_COMPOSE = RuleSetDescriptor(
    discriminator        = 'Type',
    list_fields          = {'Depends', 'Networks', 'Ports', 'Volumes', 'Network_members'},
    ref_fields           = {'Depends', 'Networks', 'Network_members'},
    allow_unknown_fields = False,
    command_builder      = build_podman_compose_command,
    unit_file_builder    = create_podman_compose_unit_file,
    manifest_builder     = build_podman_compose_manifest_entry,
    schemas              = {
        _PODMAN_COMPOSE_SERVICE_TYPE : _PODMAN_COMPOSE_SERVICE_SCHEMA,
        _PODMAN_COMPOSE_NETWORK_TYPE : _PODMAN_COMPOSE_NETWORK_SCHEMA,
    },
)


# ---------------------------------------------------------------------------
# Active rule-set selection.
# ---------------------------------------------------------------------------
ACTIVE_RULE_SET = PODMAN_COMPOSE
