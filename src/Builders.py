from __future__ import annotations
from dataclasses import dataclass, field
from RelationshipMapping import (
    compute_before, compute_group_maps, validate_dag,
    compute_unified_before, validate_unified_dag,
)
from GraphVisualizer     import draw_dependency_graph, draw_group_graph
from UnitFileCreator    import Unit_File
from ManifestWriter      import ManifestEntry
from Schemas              import (
    _GENERIC_TASK_LAUNCHER_ENTRY_TYPE,
    _GENERIC_TASK_LAUNCHER_GROUP_TYPE,
    _GENERIC_TASK_LAUNCHER_FAILURE_BEHAVIOR_ABORT,
    _GENERIC_TASK_LAUNCHER_FAILURE_BEHAVIOR_RESTART,
    _GENERIC_TASK_LAUNCHER_DEPENDENCY_BEHAVIOR_IGNORE,
    _GENERIC_TASK_LAUNCHER_DEPENDENCY_BEHAVIOR_CASCADE,
    _PODMAN_COMPOSE_SERVICE_TYPE,
    _PODMAN_COMPOSE_NETWORK_TYPE,
    _PODMAN_COMPOSE_FAILURE_BEHAVIOR_ABORT,
    _PODMAN_COMPOSE_FAILURE_BEHAVIOR_RESTART,
    _PODMAN_COMPOSE_DEPENDENCY_BEHAVIOR_IGNORE,
    _PODMAN_COMPOSE_DEPENDENCY_BEHAVIOR_CASCADE,
)


#############################################################################
#############################################################################
# BEGIN GENERIC TASK LAUNCHER
#############################################################################
#############################################################################

# ---------------------------------------------------------------------------
# Reserved field names for the generic-task-launcher environment.
# ---------------------------------------------------------------------------

_GENERIC_TASK_LAUNCHER_RESERVED_FIELDS = {
    'Name', 'Path', 'Type', 'Depends', 'Group', 'Members', 'Order',
    'FailureBehavior', 'DependencyBehavior',
}

# ---------------------------------------------------------------------------
# Field name constants for the generic-task-launcher environment.
# ---------------------------------------------------------------------------

_GENERIC_TASK_LAUNCHER_DEPENDS_FIELD             = 'Depends'
_GENERIC_TASK_LAUNCHER_GROUP_FIELD               = 'Group'
_GENERIC_TASK_LAUNCHER_MEMBERS_FIELD             = 'Members'
_GENERIC_TASK_LAUNCHER_FAILURE_BEHAVIOR_FIELD    = 'FailureBehavior'
_GENERIC_TASK_LAUNCHER_DEPENDENCY_BEHAVIOR_FIELD = 'DependencyBehavior'

# Hardcoded fallbacks, used when neither the YAML field nor the Launcher.config
# default supplies a value. Overridden at UnitGenerator entry by the matching
# set_generic_task_launcher_*_default() call.
_generic_task_launcher_failure_behavior_default    = \
    _GENERIC_TASK_LAUNCHER_FAILURE_BEHAVIOR_ABORT
_generic_task_launcher_dependency_behavior_default = \
    _GENERIC_TASK_LAUNCHER_DEPENDENCY_BEHAVIOR_IGNORE

# Own-axis directive values for FailureBehavior=Restart.
_GENERIC_TASK_LAUNCHER_RESTART_POLICY           = 'on-failure'
_GENERIC_TASK_LAUNCHER_RESTART_SEC              = '2'
_GENERIC_TASK_LAUNCHER_START_LIMIT_BURST        = '5'
_GENERIC_TASK_LAUNCHER_START_LIMIT_INTERVAL_SEC = '30'


def set_generic_task_launcher_failure_behavior_default(value: str) -> None:
    """
    Overrides the module-level FailureBehavior default used as the fallback
    inside construct_generic_command when an instance has no FailureBehavior
    field of its own. Called once by UnitGenerator.parse_args after resolving the
    FAILURE_BEHAVIOR_DEFAULT cmdline arg.
    """
    global _generic_task_launcher_failure_behavior_default
    if value not in (
        _GENERIC_TASK_LAUNCHER_FAILURE_BEHAVIOR_ABORT,
        _GENERIC_TASK_LAUNCHER_FAILURE_BEHAVIOR_RESTART,
    ):
        raise ValueError(
            f"Invalid FailureBehaviorDefault {value!r}; "
            f"expected 'Abort' or 'Restart'."
        )
    _generic_task_launcher_failure_behavior_default = value


def set_generic_task_launcher_dependency_behavior_default(value: str) -> None:
    """
    Overrides the module-level DependencyBehavior default used as the fallback
    inside construct_generic_command when an instance has no DependencyBehavior
    field of its own. Called once by UnitGenerator.parse_args after resolving the
    DEPENDENCY_BEHAVIOR_DEFAULT cmdline arg.
    """
    global _generic_task_launcher_dependency_behavior_default
    if value not in (
        _GENERIC_TASK_LAUNCHER_DEPENDENCY_BEHAVIOR_IGNORE,
        _GENERIC_TASK_LAUNCHER_DEPENDENCY_BEHAVIOR_CASCADE,
    ):
        raise ValueError(
            f"Invalid DependencyBehaviorDefault {value!r}; "
            f"expected 'Ignore' or 'Cascade'."
        )
    _generic_task_launcher_dependency_behavior_default = value

# ---------------------------------------------------------------------------
# Unit file extension map for the generic-task-launcher environment.
# ---------------------------------------------------------------------------

_GENERIC_TASK_LAUNCHER_UNIT_EXTENSIONS = {
    _GENERIC_TASK_LAUNCHER_ENTRY_TYPE : '.service',
    _GENERIC_TASK_LAUNCHER_GROUP_TYPE : '.target',
}

# ---------------------------------------------------------------------------
# Manifest type string map for the generic-task-launcher environment.
# Maps discriminator values to the uppercase type strings the C parser
# expects in the manifest file.
# ---------------------------------------------------------------------------

_GENERIC_TASK_LAUNCHER_MANIFEST_TYPES = {
    _GENERIC_TASK_LAUNCHER_ENTRY_TYPE : 'ENTRY',
    _GENERIC_TASK_LAUNCHER_GROUP_TYPE : 'GROUP',
}


@dataclass
class GeneratedCommand:
    """
    Holds the fully parsed and structured representation of one instance
    in the generic-task-launcher environment.

    Fields:
        key                : top-level YAML key (e.g. 'Program1', 'Group1').
        name               : value of the Name field.
        type_              : value of the Type field (discriminator).
        path               : absolute path to the executable; None for Group instances.
        unit_name          : same as the Name field value.
        unit_file_name     : systemd unit file name derived from unit_name and type_.
        depends            : raw Depends field value from the input.
        extra_args         : ordered dict of user-defined argument fields.
        after              : instances this instance requires (copy of depends).
        before             : instances that require this instance (inverted depends).
        group              : complete list of groups this instance belongs to.
        members            : complete list of members this instance contains.
        failure_behavior   : FailureBehavior value in effect for this instance
                             — Abort or Restart (own axis).
        dependency_behavior: DependencyBehavior value in effect for this
                             instance — Ignore or Cascade (dependency axis).
    """
    key                 : str
    name                : str
    type_               : str
    path                : str | None     = None
    unit_name           : str | None     = None
    unit_file_name      : str | None     = None
    depends             : list[str]      = field(default_factory=list)
    extra_args          : dict[str, str] = field(default_factory=dict)
    after               : list[str]      = field(default_factory=list)
    before              : list[str]      = field(default_factory=list)
    group               : list[str]      = field(default_factory=list)
    members             : list[str]      = field(default_factory=list)
    order               : int            = -1
    failure_behavior    : str            = 'Abort'
    dependency_behavior : str            = 'Ignore'

    def command_string(self) -> str:
        """
        Builds the executable command string for Entry instances.
        Returns an empty string for Group instances (no Path defined).
        """
        if self.path is None:
            return ''
        tokens = [self.path] + list(self.extra_args.values())
        return ' '.join(tokens)


def _derive_unit_file_name(unit_name: str, type_: str) -> str:
    extension = _GENERIC_TASK_LAUNCHER_UNIT_EXTENSIONS.get(type_, '')
    return unit_name + extension


def _build_unit_file_name_map(all_instances: list) -> dict[str, str]:
    return {instance.key: instance.unit_file_name for instance in all_instances}


def _resolve_unit_file_names(keys: list[str], key_map: dict[str, str]) -> str:
    return ' '.join(
        key_map[key]
        for key in keys
        if key in key_map
    )


def construct_generic_command(
    key        : str,
    fields     : dict,
    before_map : dict[str, list[str]],
    group_of   : dict[str, list[str]],
    members_of : dict[str, list[str]],
) -> GeneratedCommand:
    """
    Pure construction: builds a GeneratedCommand from one instance's
    fields and the pre-computed relationship maps.

    No side effects — does not compute mappings, validate DAGs,
    or draw graphs.

    FailureBehavior / DependencyBehavior resolution chain (for each):
        1. YAML per-instance field, if present;
        2. else module-level default, set by UnitGenerator from the matching
           *_BEHAVIOR_DEFAULT cmdline arg;
        3. else hardcoded default (Abort / Ignore respectively).
    """
    extra_args = {
        field_name: value
        for field_name, value in fields.items()
        if field_name not in _GENERIC_TASK_LAUNCHER_RESERVED_FIELDS
    }

    depends             = fields.get(_GENERIC_TASK_LAUNCHER_DEPENDS_FIELD, [])
    type_               = fields.get('Type')
    unit_name           = fields.get('Name')
    failure_behavior    = fields.get(
        _GENERIC_TASK_LAUNCHER_FAILURE_BEHAVIOR_FIELD,
        _generic_task_launcher_failure_behavior_default,
    )
    dependency_behavior = fields.get(
        _GENERIC_TASK_LAUNCHER_DEPENDENCY_BEHAVIOR_FIELD,
        _generic_task_launcher_dependency_behavior_default,
    )

    return GeneratedCommand(
        key                 = key,
        name                = unit_name,
        type_               = type_,
        path                = fields.get('Path'),
        unit_name           = unit_name,
        unit_file_name      = _derive_unit_file_name(unit_name, type_),
        depends             = depends,
        extra_args          = extra_args,
        after               = depends,
        before              = before_map.get(key, []),
        group               = group_of.get(key, []),
        members             = members_of.get(key, []),
        failure_behavior    = failure_behavior,
        dependency_behavior = dependency_behavior,
    )


def build_generic_task_launcher_command(
    key          : str,
    fields       : dict,
    all_instances: dict,
) -> GeneratedCommand:
    """
    Orchestrator: computes relationship maps, validates DAGs, draws
    graphs (once per input set), then delegates to
    construct_generic_command for object construction.
    """
    if not hasattr(build_generic_task_launcher_command, '_cached_for'):
        build_generic_task_launcher_command._cached_for = None

    if build_generic_task_launcher_command._cached_for is not all_instances:
        build_generic_task_launcher_command._before_map = compute_before(
            all_instances,
            _GENERIC_TASK_LAUNCHER_DEPENDS_FIELD,
        )
        build_generic_task_launcher_command._group_of, \
        build_generic_task_launcher_command._members_of = compute_group_maps(
            all_instances,
            _GENERIC_TASK_LAUNCHER_GROUP_FIELD,
            _GENERIC_TASK_LAUNCHER_MEMBERS_FIELD,
        )
        draw_dependency_graph(build_generic_task_launcher_command._before_map)
        draw_group_graph(
            build_generic_task_launcher_command._group_of,
            build_generic_task_launcher_command._members_of,
        )
        validate_dag(
            all_instances,
            _GENERIC_TASK_LAUNCHER_DEPENDS_FIELD,
            _GENERIC_TASK_LAUNCHER_GROUP_FIELD,
            _GENERIC_TASK_LAUNCHER_MEMBERS_FIELD,
        )
        build_generic_task_launcher_command._unified_before = compute_unified_before(
            build_generic_task_launcher_command._before_map,
            build_generic_task_launcher_command._group_of,
        )
        validate_unified_dag(
            build_generic_task_launcher_command._unified_before,
        )
        draw_dependency_graph(
            build_generic_task_launcher_command._unified_before,
            graph_name='unified_graph',
            graph_label='Unified Dependency + Group Graph',
        )
        build_generic_task_launcher_command._cached_for = all_instances

    return construct_generic_command(
        key,
        fields,
        build_generic_task_launcher_command._before_map,
        build_generic_task_launcher_command._group_of,
        build_generic_task_launcher_command._members_of,
    )


def create_generic_task_launcher_unit_file(
    instance     : GeneratedCommand,
    all_instances: list,
    output_path  : str,
) -> None:
    """
    Creates and dumps one systemd unit file for a generic-task-launcher
    instance. Emission is per-child (child's own values drive everything):

    Own axis (FailureBehavior):
        Abort   → nothing (systemd default Restart=no; unit goes 'failed').
        Restart → Restart=on-failure + RestartSec + StartLimitBurst +
                  StartLimitIntervalSec. Entry instances only.

    Dependency axis (DependencyBehavior), applied to every parent edge:
        Ignore  → Requires=<parent>, After=<parent>.
        Cascade → Requires=<parent>, After=<parent>, PartOf=<parent>.
    """
    key_map = _build_unit_file_name_map(all_instances)

    before_keys = list(dict.fromkeys(instance.before + instance.group))
    after_keys  = list(dict.fromkeys(instance.after  + instance.members))

    before_value = _resolve_unit_file_names(before_keys, key_map)
    after_value  = _resolve_unit_file_names(after_keys,  key_map)

    uf = Unit_File()

    uf.edit_field('UNIT', 'Description', instance.unit_file_name + ' UNIT FILE')

    if before_value:
        uf.edit_field('UNIT',    'Before',     before_value)
        uf.edit_field('INSTALL', 'RequiredBy', before_value)

    if after_value:
        uf.edit_field('UNIT', 'After',    after_value)
        uf.edit_field('UNIT', 'Requires', after_value)

    if (instance.dependency_behavior ==
            _GENERIC_TASK_LAUNCHER_DEPENDENCY_BEHAVIOR_CASCADE
        and after_value):
        uf.edit_field('UNIT', 'PartOf', after_value)

    if instance.type_ == _GENERIC_TASK_LAUNCHER_ENTRY_TYPE:
        uf.edit_field('SERVICE', 'ExecStart', instance.command_string())

        if instance.failure_behavior == _GENERIC_TASK_LAUNCHER_FAILURE_BEHAVIOR_RESTART:
            uf.edit_field('SERVICE', 'Restart',               _GENERIC_TASK_LAUNCHER_RESTART_POLICY)
            uf.edit_field('SERVICE', 'RestartSec',            _GENERIC_TASK_LAUNCHER_RESTART_SEC)
            uf.edit_field('UNIT',    'StartLimitBurst',       _GENERIC_TASK_LAUNCHER_START_LIMIT_BURST)
            uf.edit_field('UNIT',    'StartLimitIntervalSec', _GENERIC_TASK_LAUNCHER_START_LIMIT_INTERVAL_SEC)

    uf.dump_unit_file(instance.unit_file_name, output_path)


def build_generic_task_launcher_manifest_entry(
    instance: GeneratedCommand,
) -> ManifestEntry:
    """
    Maps one GeneratedCommand to a ManifestEntry for the
    generic-task-launcher environment.

    type_ is mapped to the uppercase string the C parser expects via
    _GENERIC_TASK_LAUNCHER_MANIFEST_TYPES. path and command are passed
    as empty strings for Group instances. All list fields pass through
    directly since they already hold instance keys.
    """
    manifest_type = _GENERIC_TASK_LAUNCHER_MANIFEST_TYPES.get(instance.type_, '')

    return ManifestEntry(
        key                 = instance.key,
        name                = instance.name,
        unit_file_name      = instance.unit_file_name,
        type_               = manifest_type,
        path                = instance.path    or '',
        command             = instance.command_string(),
        after               = instance.after,
        before              = instance.before,
        group               = instance.group,
        members             = instance.members,
        order               = instance.order,
        failure_behavior    = instance.failure_behavior,
        dependency_behavior = instance.dependency_behavior,
    )


#############################################################################
#############################################################################
# END GENERIC TASK LAUNCHER
#############################################################################
#############################################################################

#############################################################################
#############################################################################
# BEGIN PODMAN COMPOSE
#############################################################################
#############################################################################

# ---------------------------------------------------------------------------
# Field name constants for the podman-compose environment.
# Passed to generic mapping functions so those files carry no rule-set
# knowledge.
# ---------------------------------------------------------------------------

_PODMAN_COMPOSE_DEPENDS_FIELD             = 'Depends'
_PODMAN_COMPOSE_NETWORKS_FIELD            = 'Networks'
_PODMAN_COMPOSE_NETWORK_MEMBERS_FIELD     = 'Network_members'
_PODMAN_COMPOSE_FAILURE_BEHAVIOR_FIELD    = 'FailureBehavior'
_PODMAN_COMPOSE_DEPENDENCY_BEHAVIOR_FIELD = 'DependencyBehavior'

# ---------------------------------------------------------------------------
# Reserved field names for the podman-compose environment.
# ---------------------------------------------------------------------------

_PODMAN_COMPOSE_RESERVED_FIELDS = {
    'Name', 'Type', 'Image', 'ContainerName',
    'Depends', 'Networks', 'Ports', 'Volumes',
    'Environment', 'Command', 'Entrypoint', 'Working_dir',
    'Network_members', 'FailureBehavior', 'DependencyBehavior',
}

# Hardcoded fallbacks, used when neither the YAML field nor the Launcher.config
# default supplies a value. Overridden at UnitGenerator entry by the matching
# set_podman_compose_*_default() call.
_podman_compose_failure_behavior_default    = \
    _PODMAN_COMPOSE_FAILURE_BEHAVIOR_ABORT
_podman_compose_dependency_behavior_default = \
    _PODMAN_COMPOSE_DEPENDENCY_BEHAVIOR_IGNORE

# Own-axis directive values for FailureBehavior=Restart.
_PODMAN_COMPOSE_RESTART_POLICY           = 'on-failure'
_PODMAN_COMPOSE_RESTART_SEC              = '2'
_PODMAN_COMPOSE_START_LIMIT_BURST        = '5'
_PODMAN_COMPOSE_START_LIMIT_INTERVAL_SEC = '30'


def set_podman_compose_failure_behavior_default(value: str) -> None:
    """
    Overrides the module-level FailureBehavior default used as the fallback
    inside construct_podman_command when an instance has no FailureBehavior
    field of its own. Called once by UnitGenerator.parse_args after resolving the
    FAILURE_BEHAVIOR_DEFAULT cmdline arg.
    """
    global _podman_compose_failure_behavior_default
    if value not in (
        _PODMAN_COMPOSE_FAILURE_BEHAVIOR_ABORT,
        _PODMAN_COMPOSE_FAILURE_BEHAVIOR_RESTART,
    ):
        raise ValueError(
            f"Invalid FailureBehaviorDefault {value!r}; "
            f"expected 'Abort' or 'Restart'."
        )
    _podman_compose_failure_behavior_default = value


def set_podman_compose_dependency_behavior_default(value: str) -> None:
    """
    Overrides the module-level DependencyBehavior default used as the fallback
    inside construct_podman_command when an instance has no DependencyBehavior
    field of its own. Called once by UnitGenerator.parse_args after resolving the
    DEPENDENCY_BEHAVIOR_DEFAULT cmdline arg.
    """
    global _podman_compose_dependency_behavior_default
    if value not in (
        _PODMAN_COMPOSE_DEPENDENCY_BEHAVIOR_IGNORE,
        _PODMAN_COMPOSE_DEPENDENCY_BEHAVIOR_CASCADE,
    ):
        raise ValueError(
            f"Invalid DependencyBehaviorDefault {value!r}; "
            f"expected 'Ignore' or 'Cascade'."
        )
    _podman_compose_dependency_behavior_default = value

# ---------------------------------------------------------------------------
# Unit file extension map for the podman-compose environment.
# Both Service and Network emit .service files — Network is library-specific
# in that it also requires a runtime command (`podman network create`),
# so unlike GTL's passive Group (.target), it is materialized as a .service.
# ---------------------------------------------------------------------------

_PODMAN_COMPOSE_UNIT_EXTENSIONS = {
    _PODMAN_COMPOSE_SERVICE_TYPE : '.service',
    _PODMAN_COMPOSE_NETWORK_TYPE : '.service',
}

# ---------------------------------------------------------------------------
# Manifest type string map for the podman-compose environment.
# Maps discriminator values to the uppercase type strings used in the
# manifest file.
# ---------------------------------------------------------------------------

_PODMAN_COMPOSE_MANIFEST_TYPES = {
    _PODMAN_COMPOSE_SERVICE_TYPE : 'SERVICE',
    _PODMAN_COMPOSE_NETWORK_TYPE : 'NETWORK',
}

# ---------------------------------------------------------------------------
# PodmanGeneratedCommand — runtime representation of one podman-compose
# Service instance.
# ---------------------------------------------------------------------------

@dataclass
class PodmanGeneratedCommand:
    """
    Holds the fully parsed and structured representation of one
    podman-compose instance. Covers both Service (container) and Network
    Types; container-only fields (image, container_name, ports, volumes,
    environment, command, entrypoint, working_dir) are left empty for
    Network instances.

    Fields:
        key                : top-level YAML key (e.g. 'WebServer').
        name               : value of the Name field.
        type_              : value of the Type field ('Service' or 'Network').
        unit_file_name     : <name>.service (both Types emit .service).
        image              : container image reference (Service only).
        container_name     : value of ContainerName — used for --name,
                             ExecStop, and ExecStopPost (Service only).
        depends            : raw Depends field value.
        networks           : networks this service joins (Service only).
        ports              : list of HOST:CONTAINER port mapping strings.
        volumes            : list of source:target:options volume strings.
        environment        : dict of environment variable KEY:VALUE pairs.
        command            : overrides image CMD — string or list joined to string.
        entrypoint         : overrides image ENTRYPOINT — string or list joined.
        working_dir        : container working directory.
        after              : instances this instance requires (copy of depends).
        before             : instances that require this instance.
        group              : networks this instance belongs to (reconciled).
        network_members    : instances this network contains (reconciled).
        failure_behavior   : FailureBehavior value in effect — Abort or Restart.
        dependency_behavior: DependencyBehavior value in effect — Ignore or Cascade.
        unit_extension     : '.service' (both Types).
        exec_start         : ExecStart= command string built from Type-specific
                             fields (`podman run ...` for Service, `podman
                             network create --ignore <name>` for Network).
        exec_stop          : ExecStop= command string.
        exec_stop_post     : ExecStopPost= command string (Service only;
                             empty for Network).
    """
    key                 : str
    name                : str
    type_               : str
    unit_file_name      : str
    image               : str            = ''
    container_name      : str            = ''
    depends             : list[str]      = field(default_factory=list)
    networks            : list[str]      = field(default_factory=list)
    ports               : list[str]      = field(default_factory=list)
    volumes             : list[str]      = field(default_factory=list)
    environment         : dict[str, str] = field(default_factory=dict)
    command             : str            = ''
    entrypoint          : str            = ''
    working_dir         : str            = ''
    after               : list[str]      = field(default_factory=list)
    before              : list[str]      = field(default_factory=list)
    group               : list[str]      = field(default_factory=list)
    network_members     : list[str]      = field(default_factory=list)
    order               : int            = -1
    failure_behavior    : str            = 'Abort'
    dependency_behavior : str            = 'Ignore'
    unit_extension      : str            = '.service'
    exec_start          : str            = ''
    exec_stop           : str            = ''
    exec_stop_post      : str            = ''


def _build_podman_run_command(fields: dict) -> str:
    """
    Constructs the full podman run command string from validated instance
    fields. Field order follows the agreed mapping:
        --name, --network, -p, -v, -e, --entrypoint, --workdir, image, command
    """
    parts = ['podman run']

    parts.append(f'--name {fields["ContainerName"]}')

    for net in fields.get('Networks', []):
        parts.append(f'--network {net}')

    for port in fields.get('Ports', []):
        parts.append(f'-p {port}')

    for vol in fields.get('Volumes', []):
        parts.append(f'-v {vol}')

    for key, value in fields.get('Environment', {}).items():
        parts.append(f'-e {key}={value}')

    entrypoint = fields.get('Entrypoint', '')
    if isinstance(entrypoint, list):
        entrypoint = ' '.join(entrypoint)
    if entrypoint:
        parts.append(f'--entrypoint {entrypoint}')

    working_dir = fields.get('Working_dir', '')
    if working_dir:
        parts.append(f'--workdir {working_dir}')

    parts.append(fields['Image'])

    command = fields.get('Command', '')
    if isinstance(command, list):
        command = ' '.join(command)
    if command:
        parts.append(command)

    return ' '.join(parts)


def construct_podman_command(
    key        : str,
    fields     : dict,
    before_map : dict[str, list[str]],
    group_of   : dict[str, list[str]],
    members_of : dict[str, list[str]],
) -> PodmanGeneratedCommand:
    """
    Pure construction: builds a PodmanGeneratedCommand from one
    instance's fields and the pre-computed relationship maps.

    No side effects — does not compute mappings, validate DAGs,
    or draw graphs.

    FailureBehavior / DependencyBehavior resolution chain (for each):
        1. YAML per-instance field, if present;
        2. else module-level default, set by UnitGenerator from the matching
           *_BEHAVIOR_DEFAULT cmdline arg;
        3. else hardcoded default (Abort / Ignore respectively).

    ExecStart / ExecStop / ExecStopPost are derived per Type:
        Service : podman run ..., podman stop <CN>, podman rm <CN>.
        Network : podman network create --ignore <Name>,
                  podman network rm <Name>, (empty).
    """
    name  = fields['Name']
    type_ = fields['Type']

    unit_extension = _PODMAN_COMPOSE_UNIT_EXTENSIONS.get(type_, '.service')
    unit_file_name = name + unit_extension

    depends = fields.get(_PODMAN_COMPOSE_DEPENDS_FIELD, [])

    failure_behavior = fields.get(
        _PODMAN_COMPOSE_FAILURE_BEHAVIOR_FIELD,
        _podman_compose_failure_behavior_default,
    )
    dependency_behavior = fields.get(
        _PODMAN_COMPOSE_DEPENDENCY_BEHAVIOR_FIELD,
        _podman_compose_dependency_behavior_default,
    )

    if type_ == _PODMAN_COMPOSE_SERVICE_TYPE:
        image          = fields['Image']
        container_name = fields['ContainerName']
        networks       = fields.get(_PODMAN_COMPOSE_NETWORKS_FIELD, [])
        ports          = fields.get('Ports',       [])
        volumes        = fields.get('Volumes',     [])
        environment    = fields.get('Environment', {})
        working_dir    = fields.get('Working_dir', '')

        entrypoint = fields.get('Entrypoint', '')
        if isinstance(entrypoint, list):
            entrypoint = ' '.join(entrypoint)

        command = fields.get('Command', '')
        if isinstance(command, list):
            command = ' '.join(command)

        exec_start     = _build_podman_run_command(fields)
        exec_stop      = f'podman stop {container_name}'
        exec_stop_post = f'podman rm {container_name}'
    else:
        image          = ''
        container_name = ''
        networks       = []
        ports          = []
        volumes        = []
        environment    = {}
        entrypoint     = ''
        command        = ''
        working_dir    = ''

        exec_start     = f'podman network create --ignore {name}'
        exec_stop      = f'podman network rm {name}'
        exec_stop_post = ''

    return PodmanGeneratedCommand(
        key                 = key,
        name                = name,
        type_               = type_,
        unit_file_name      = unit_file_name,
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
        after               = depends,
        before              = before_map.get(key, []),
        group               = group_of.get(key,  []),
        network_members     = members_of.get(key, []),
        failure_behavior    = failure_behavior,
        dependency_behavior = dependency_behavior,
        unit_extension      = unit_extension,
        exec_start          = exec_start,
        exec_stop           = exec_stop,
        exec_stop_post      = exec_stop_post,
    )


def build_podman_compose_command(
    key          : str,
    fields       : dict,
    all_instances: dict,
) -> PodmanGeneratedCommand:
    """
    Orchestrator: computes relationship maps, validates DAGs, draws
    graphs (once per input set), then delegates to
    construct_podman_command for object construction.
    """
    if not hasattr(build_podman_compose_command, '_cached_for'):
        build_podman_compose_command._cached_for = None

    if build_podman_compose_command._cached_for is not all_instances:
        build_podman_compose_command._before_map = compute_before(
            all_instances,
            _PODMAN_COMPOSE_DEPENDS_FIELD,
        )
        build_podman_compose_command._group_of, \
        build_podman_compose_command._members_of = compute_group_maps(
            all_instances,
            _PODMAN_COMPOSE_NETWORKS_FIELD,
            _PODMAN_COMPOSE_NETWORK_MEMBERS_FIELD,
        )
        draw_dependency_graph(build_podman_compose_command._before_map)
        draw_group_graph(
            build_podman_compose_command._group_of,
            build_podman_compose_command._members_of,
        )
        validate_dag(
            all_instances,
            _PODMAN_COMPOSE_DEPENDS_FIELD,
            _PODMAN_COMPOSE_NETWORKS_FIELD,
            _PODMAN_COMPOSE_NETWORK_MEMBERS_FIELD,
        )
        build_podman_compose_command._unified_before = compute_unified_before(
            build_podman_compose_command._before_map,
            build_podman_compose_command._group_of,
        )
        validate_unified_dag(
            build_podman_compose_command._unified_before,
        )
        draw_dependency_graph(
            build_podman_compose_command._unified_before,
            graph_name='unified_graph',
            graph_label='Unified Dependency + Group Graph',
        )
        build_podman_compose_command._cached_for = all_instances

    return construct_podman_command(
        key,
        fields,
        build_podman_compose_command._before_map,
        build_podman_compose_command._group_of,
        build_podman_compose_command._members_of,
    )


def create_podman_compose_unit_file(
    instance     : PodmanGeneratedCommand,
    all_instances: list,
    output_path  : str,
) -> None:
    """
    Creates and dumps one systemd unit file for a podman-compose instance.
    Both Service and Network Types emit .service files. Emission is
    per-child (child's own values drive everything).

    Own axis (FailureBehavior):
        Abort   → nothing (systemd default Restart=no; unit goes 'failed').
        Restart → Restart=on-failure + RestartSec + StartLimitBurst +
                  StartLimitIntervalSec.

    Dependency axis (DependencyBehavior), applied to every parent edge:
        Ignore  → Requires=<parent>, After=<parent>.
        Cascade → Requires=<parent>, After=<parent>, PartOf=<parent>.

    [Service] differs per Type:
        Service : Type=simple; ExecStart=podman run ...;
                  ExecStop/ExecStopPost reference ContainerName.
        Network : Type=oneshot + RemainAfterExit=yes;
                  ExecStart=podman network create --ignore <Name>;
                  ExecStop=podman network rm <Name>;
                  no ExecStopPost.
    """
    key_map = _build_unit_file_name_map(all_instances)

    before_keys = list(dict.fromkeys(instance.before + instance.group))
    after_keys  = list(dict.fromkeys(instance.after  + instance.network_members))

    before_value = _resolve_unit_file_names(before_keys, key_map)
    after_value  = _resolve_unit_file_names(after_keys,  key_map)

    uf = Unit_File()

    uf.edit_field('UNIT', 'Description', instance.unit_file_name + ' UNIT FILE')

    if before_value:
        uf.edit_field('UNIT',    'Before',     before_value)
        uf.edit_field('INSTALL', 'RequiredBy', before_value)

    if after_value:
        uf.edit_field('UNIT', 'After',    after_value)
        uf.edit_field('UNIT', 'Requires', after_value)

    if (instance.dependency_behavior ==
            _PODMAN_COMPOSE_DEPENDENCY_BEHAVIOR_CASCADE
        and after_value):
        uf.edit_field('UNIT', 'PartOf', after_value)

    if instance.type_ == _PODMAN_COMPOSE_SERVICE_TYPE:
        uf.edit_field('SERVICE', 'Type', 'simple')
    else:
        uf.edit_field('SERVICE', 'Type',            'oneshot')
        uf.edit_field('SERVICE', 'RemainAfterExit', 'yes')

    uf.edit_field('SERVICE', 'ExecStart', instance.exec_start)
    uf.edit_field('SERVICE', 'ExecStop',  instance.exec_stop)
    if instance.exec_stop_post:
        uf.edit_field('SERVICE', 'ExecStopPost', instance.exec_stop_post)

    if instance.failure_behavior == _PODMAN_COMPOSE_FAILURE_BEHAVIOR_RESTART:
        uf.edit_field('SERVICE', 'Restart',               _PODMAN_COMPOSE_RESTART_POLICY)
        uf.edit_field('SERVICE', 'RestartSec',            _PODMAN_COMPOSE_RESTART_SEC)
        uf.edit_field('UNIT',    'StartLimitBurst',       _PODMAN_COMPOSE_START_LIMIT_BURST)
        uf.edit_field('UNIT',    'StartLimitIntervalSec', _PODMAN_COMPOSE_START_LIMIT_INTERVAL_SEC)

    uf.dump_unit_file(instance.unit_file_name, output_path)


def build_podman_compose_manifest_entry(
    instance: PodmanGeneratedCommand,
) -> ManifestEntry:
    """
    Maps one PodmanGeneratedCommand to a ManifestEntry for the
    podman-compose environment.

    type_ is mapped to the uppercase string the C parser expects via
    _PODMAN_COMPOSE_MANIFEST_TYPES. command carries the ExecStart string
    (podman run ... for Service, podman network create ... for Network).
    """
    manifest_type = _PODMAN_COMPOSE_MANIFEST_TYPES.get(instance.type_, '')

    return ManifestEntry(
        key                 = instance.key,
        name                = instance.name,
        unit_file_name      = instance.unit_file_name,
        type_               = manifest_type,
        path                = '',
        command             = instance.exec_start,
        after               = instance.after,
        before              = instance.before,
        group               = instance.group,
        members             = instance.network_members,
        order               = instance.order,
        failure_behavior    = instance.failure_behavior,
        dependency_behavior = instance.dependency_behavior,
    )


#############################################################################
#############################################################################
# END PODMAN COMPOSE
#############################################################################
#############################################################################
