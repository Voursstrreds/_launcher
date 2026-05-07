# ---------------------------------------------------------------------------
# Schemas — pure syntactic definition of well-formed inputs for both
# launcher rule-sets. Discriminator type values and allowed behavior
# values live here too: they form the vocabulary the schemas enumerate
# and are consumed by Builders.py.
# ---------------------------------------------------------------------------


#############################################################################
#############################################################################
# BEGIN GENERIC TASK LAUNCHER
#############################################################################
#############################################################################

# ---------------------------------------------------------------------------
# Discriminator value constants for the generic-task-launcher environment.
# ---------------------------------------------------------------------------

_GENERIC_TASK_LAUNCHER_ENTRY_TYPE = 'Entry'
_GENERIC_TASK_LAUNCHER_GROUP_TYPE = 'Group'

# ---------------------------------------------------------------------------
# Failure policy — two independent, child-owned axes:
#
#   FailureBehavior    (own axis)        Abort | Restart         default Abort
#   DependencyBehavior (dependency axis) Ignore | Cascade        default Ignore
#
# Own axis — how the instance reacts to its own crash:
#   Abort    : emit nothing (systemd default Restart=no); unit goes 'failed'.
#   Restart  : emit Restart=on-failure plus rate-limit knobs.
# (Not applicable to Group instances, since .target files have no Service
#  section. The Group schema rejects FailureBehavior=Restart.)
#
# Dependency axis — how the instance reacts to a parent going down/restarting,
# emitted for every parent edge (after_keys):
#   Ignore   : Requires=<parent>, After=<parent>.
#   Cascade  : Requires=<parent>, After=<parent>, PartOf=<parent>.
# ---------------------------------------------------------------------------

_GENERIC_TASK_LAUNCHER_FAILURE_BEHAVIOR_ABORT   = 'Abort'
_GENERIC_TASK_LAUNCHER_FAILURE_BEHAVIOR_RESTART = 'Restart'

_GENERIC_TASK_LAUNCHER_DEPENDENCY_BEHAVIOR_IGNORE  = 'Ignore'
_GENERIC_TASK_LAUNCHER_DEPENDENCY_BEHAVIOR_CASCADE = 'Cascade'

# ---------------------------------------------------------------------------
# Cerberus sub-schemas for the generic-task-launcher environment
# ---------------------------------------------------------------------------

_ENTRY_SCHEMA = {
    'Name': {
        'type': 'string',
        'required': True,
    },
    'Path': {
        'type': 'string',
        'required': True,
    },
    'Type': {
        'type': 'string',
        'required': True,
        'allowed': ['Entry'],
    },
    'Depends': {
        'type': 'list',
        'required': False,
        'schema': {'type': 'string'},
    },
    'Group': {
        'type': 'list',
        'required': False,
        'schema': {'type': 'string'},
    },
    'FailureBehavior': {
        'type': 'string',
        'required': False,
        'allowed': ['Abort', 'Restart'],
    },
    'DependencyBehavior': {
        'type': 'string',
        'required': False,
        'allowed': ['Ignore', 'Cascade'],
    },
}

_GROUP_SCHEMA = {
    'Name': {
        'type': 'string',
        'required': True,
    },
    'Type': {
        'type': 'string',
        'required': True,
        'allowed': ['Group'],
    },
    'Depends': {
        'type': 'list',
        'required': False,
        'schema': {'type': 'string'},
    },
    'Group': {
        'type': 'list',
        'required': False,
        'schema': {'type': 'string'},
    },
    'Members': {
        'type': 'list',
        'required': False,
        'schema': {'type': 'string'},
    },
    'FailureBehavior': {
        'type': 'string',
        'required': False,
        'allowed': ['Abort'],
    },
    'DependencyBehavior': {
        'type': 'string',
        'required': False,
        'allowed': ['Ignore', 'Cascade'],
    },
}

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
# Discriminator value constants for the podman-compose environment.
# ---------------------------------------------------------------------------

_PODMAN_COMPOSE_SERVICE_TYPE = 'Service'
_PODMAN_COMPOSE_NETWORK_TYPE = 'Network'

# ---------------------------------------------------------------------------
# Failure policy — two independent, child-owned axes (mirrors GTL):
#
#   FailureBehavior    (own axis)        Abort | Restart         default Abort
#   DependencyBehavior (dependency axis) Ignore | Cascade        default Ignore
#
# Own axis — how the instance reacts to its own crash:
#   Abort    : emit nothing (systemd default Restart=no); unit goes 'failed'.
#   Restart  : emit Restart=on-failure plus rate-limit knobs.
#
# Dependency axis — how the instance reacts to a parent going down/restarting,
# emitted for every parent edge:
#   Ignore   : Requires=<parent>, After=<parent>.
#   Cascade  : Requires=<parent>, After=<parent>, PartOf=<parent>.
# ---------------------------------------------------------------------------

_PODMAN_COMPOSE_FAILURE_BEHAVIOR_ABORT   = 'Abort'
_PODMAN_COMPOSE_FAILURE_BEHAVIOR_RESTART = 'Restart'

_PODMAN_COMPOSE_DEPENDENCY_BEHAVIOR_IGNORE  = 'Ignore'
_PODMAN_COMPOSE_DEPENDENCY_BEHAVIOR_CASCADE = 'Cascade'

# ---------------------------------------------------------------------------
# Cerberus sub-schemas for the podman-compose environment
# ---------------------------------------------------------------------------

_PODMAN_COMPOSE_SERVICE_SCHEMA = {
    'Name': {
        'type': 'string',
        'required': True,
    },
    'Type': {
        'type': 'string',
        'required': True,
        'allowed': [_PODMAN_COMPOSE_SERVICE_TYPE],
    },
    'Image': {
        'type': 'string',
        'required': True,
    },
    'ContainerName': {
        'type': 'string',
        'required': True,
    },
    'Depends': {
        'type': 'list',
        'required': False,
        'schema': {'type': 'string'},
    },
    'Networks': {
        'type': 'list',
        'required': False,
        'schema': {'type': 'string'},
    },
    'Ports': {
        'type': 'list',
        'required': False,
        'schema': {'type': 'string'},
    },
    'Volumes': {
        'type': 'list',
        'required': False,
        'schema': {'type': 'string'},
    },
    'Environment': {
        'type': 'dict',
        'required': False,
        'keysrules':   {'type': 'string'},
        'valuesrules': {'type': 'string'},
    },
    'Command': {
        'required': False,
        'anyof': [
            {'type': 'string'},
            {'type': 'list', 'schema': {'type': 'string'}},
        ],
    },
    'Entrypoint': {
        'required': False,
        'anyof': [
            {'type': 'string'},
            {'type': 'list', 'schema': {'type': 'string'}},
        ],
    },
    'Working_dir': {
        'type': 'string',
        'required': False,
    },
    'FailureBehavior': {
        'type': 'string',
        'required': False,
        'allowed': [
            _PODMAN_COMPOSE_FAILURE_BEHAVIOR_ABORT,
            _PODMAN_COMPOSE_FAILURE_BEHAVIOR_RESTART,
        ],
    },
    'DependencyBehavior': {
        'type': 'string',
        'required': False,
        'allowed': [
            _PODMAN_COMPOSE_DEPENDENCY_BEHAVIOR_IGNORE,
            _PODMAN_COMPOSE_DEPENDENCY_BEHAVIOR_CASCADE,
        ],
    },
}

_PODMAN_COMPOSE_NETWORK_SCHEMA = {
    'Name': {
        'type': 'string',
        'required': True,
    },
    'Type': {
        'type': 'string',
        'required': True,
        'allowed': [_PODMAN_COMPOSE_NETWORK_TYPE],
    },
    'Depends': {
        'type': 'list',
        'required': False,
        'schema': {'type': 'string'},
    },
    'Network_members': {
        'type': 'list',
        'required': False,
        'schema': {'type': 'string'},
    },
    'FailureBehavior': {
        'type': 'string',
        'required': False,
        'allowed': [
            _PODMAN_COMPOSE_FAILURE_BEHAVIOR_ABORT,
            _PODMAN_COMPOSE_FAILURE_BEHAVIOR_RESTART,
        ],
    },
    'DependencyBehavior': {
        'type': 'string',
        'required': False,
        'allowed': [
            _PODMAN_COMPOSE_DEPENDENCY_BEHAVIOR_IGNORE,
            _PODMAN_COMPOSE_DEPENDENCY_BEHAVIOR_CASCADE,
        ],
    },
}

#############################################################################
#############################################################################
# END PODMAN COMPOSE
#############################################################################
#############################################################################
