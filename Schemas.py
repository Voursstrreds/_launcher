# ---------------------------------------------------------------------------
# Schemas — pure syntactic definition of well-formed inputs for the launcher
# rule-sets. Validation library: Cerberus.
#
#   - Podman compose: a single comprehensive nested schema validating an
#     entire podman-compose YAML document at once (services/networks/volumes/
#     configs/secrets sections, each keyed by name).
#   - Kubernetes: a single-document `kind: List` schema validating
#     Namespace / Pod / Service items via per-item `oneof` kind
#     discrimination.
# ---------------------------------------------------------------------------


#############################################################################
#############################################################################
# BEGIN PODMAN COMPOSE
#############################################################################
#############################################################################

# ---------------------------------------------------------------------------
# Reusable sub-schemas used by the service body. Many compose fields accept
# either a string-list or a dict — modeled with Cerberus `anyof`.
# ---------------------------------------------------------------------------

_LIST_OR_DICT_OF_STRINGS = {
    'anyof': [
        {'type': 'list', 'schema': {'type': 'string'}},
        {'type': 'dict', 'keysrules': {'type': 'string'}, 'valuesrules': {'type': 'string'}},
    ],
}

# environment accepts the same list-or-dict shape as labels/sysctls/etc.,
# but dict values may be int alongside string. YAML parses unquoted numeric
# values (e.g. `DB_PORT: 5432`) as int; compose-spec treats env values as
# stringified at runtime, so we allow both rather than forcing the user to
# quote every numeric value.
_ENVIRONMENT = {
    'anyof': [
        {'type': 'list', 'schema': {'type': 'string'}},
        {'type': 'dict', 'keysrules':   {'type': 'string'},
                         'valuesrules': {'anyof': [{'type': 'string'}, {'type': 'integer'}]}},
    ],
}

_STRING_OR_LIST_OF_STRINGS = {
    'anyof': [
        {'type': 'string'},
        {'type': 'list', 'schema': {'type': 'string'}},
    ],
}

_EXTERNAL_FLAG = {
    'anyof': [
        {'type': 'boolean'},
        {'type': 'dict', 'schema': {'name': {'type': 'string', 'required': False}}},
    ],
}

# build: short string (context path) or full dict.
_BUILD = {
    'anyof': [
        {'type': 'string'},
        {'type': 'dict', 'schema': {
            'context':              {'type': 'string',  'required': False},
            'dockerfile':           {'type': 'string',  'required': False},
            'dockerfile_inline':    {'type': 'string',  'required': False},
            'args':                 _LIST_OR_DICT_OF_STRINGS,
            'ssh':                  {'type': 'list',    'required': False, 'schema': {'type': 'string'}},
            'cache_from':           {'type': 'list',    'required': False, 'schema': {'type': 'string'}},
            'cache_to':             {'type': 'list',    'required': False, 'schema': {'type': 'string'}},
            'additional_contexts':  _LIST_OR_DICT_OF_STRINGS,
            'extra_hosts':          _LIST_OR_DICT_OF_STRINGS,
            'isolation':            {'type': 'string',  'required': False},
            'privileged':           {'type': 'boolean', 'required': False},
            'labels':               _LIST_OR_DICT_OF_STRINGS,
            'no_cache':             {'type': 'boolean', 'required': False},
            'pull':                 {'type': 'boolean', 'required': False},
            'network':              {'type': 'string',  'required': False},
            'shm_size':             {'anyof': [{'type': 'string'}, {'type': 'integer'}], 'required': False},
            'target':               {'type': 'string',  'required': False},
            'secrets':              {'type': 'list',    'required': False},
            'tags':                 {'type': 'list',    'required': False, 'schema': {'type': 'string'}},
            'ulimits':              {'type': 'dict',    'required': False},
            'platforms':            {'type': 'list',    'required': False, 'schema': {'type': 'string'}},
            'provenance':           {'anyof': [{'type': 'boolean'}, {'type': 'string'}], 'required': False},
            'sbom':                 {'anyof': [{'type': 'boolean'}, {'type': 'string'}], 'required': False},
        }},
    ],
}

# depends_on: short list of names, or dict-of-condition objects.
_DEPENDS_ON = {
    'anyof': [
        {'type': 'list', 'schema': {'type': 'string'}},
        {'type': 'dict', 'keysrules': {'type': 'string'}, 'valuesrules': {
            'type': 'dict', 'schema': {
                'condition': {'type': 'string', 'required': False, 'allowed': [
                                 'service_started', 'service_healthy', 'service_completed_successfully',
                              ]},
                'restart':   {'type': 'boolean', 'required': False},
                'required':  {'type': 'boolean', 'required': False},
            },
        }},
    ],
}

# deploy: large nested block. Only the well-known shape is declared.
_DEPLOY = {
    'type': 'dict', 'required': False, 'schema': {
        'endpoint_mode':   {'type': 'string', 'required': False, 'allowed': ['vip', 'dnsrr']},
        'labels':          _LIST_OR_DICT_OF_STRINGS,
        'mode':            {'type': 'string', 'required': False, 'allowed': ['global', 'replicated']},
        'replicas':        {'type': 'integer', 'required': False},
        'placement':       {'type': 'dict', 'required': False, 'schema': {
            'constraints':           {'type': 'list', 'required': False, 'schema': {'type': 'string'}},
            'preferences':           {'type': 'list', 'required': False, 'schema': {'type': 'dict'}},
            'max_replicas_per_node': {'type': 'integer', 'required': False},
        }},
        'resources':       {'type': 'dict', 'required': False, 'schema': {
            'limits':       {'type': 'dict', 'required': False, 'schema': {
                'cpus':              {'anyof': [{'type': 'string'}, {'type': 'number'}], 'required': False},
                'memory':            {'type': 'string',  'required': False},
                'pids':              {'type': 'integer', 'required': False},
            }},
            'reservations': {'type': 'dict', 'required': False, 'schema': {
                'cpus':              {'anyof': [{'type': 'string'}, {'type': 'number'}], 'required': False},
                'memory':            {'type': 'string',  'required': False},
                'generic_resources': {'type': 'list',    'required': False},
                'devices':           {'type': 'list',    'required': False},
            }},
        }},
        'restart_policy':  {'type': 'dict', 'required': False, 'schema': {
            'condition':    {'type': 'string',  'required': False, 'allowed': ['none', 'on-failure', 'any']},
            'delay':        {'type': 'string',  'required': False},
            'max_attempts': {'type': 'integer', 'required': False},
            'window':       {'type': 'string',  'required': False},
        }},
        'rollback_config': {'type': 'dict', 'required': False},
        'update_config':   {'type': 'dict', 'required': False},
    },
}

_HEALTHCHECK = {
    'type': 'dict', 'required': False, 'schema': {
        'test':           _STRING_OR_LIST_OF_STRINGS,
        'interval':       {'type': 'string',  'required': False},
        'timeout':        {'type': 'string',  'required': False},
        'retries':        {'type': 'integer', 'required': False},
        'start_period':   {'type': 'string',  'required': False},
        'start_interval': {'type': 'string',  'required': False},
        'disable':        {'type': 'boolean', 'required': False},
    },
}

_LOGGING = {
    'type': 'dict', 'required': False, 'schema': {
        'driver':  {'type': 'string', 'required': False},
        'options': {'type': 'dict',   'required': False, 'keysrules': {'type': 'string'},
                    'valuesrules': {'anyof': [{'type': 'string'}, {'type': 'integer'}, {'type': 'boolean'}, {'nullable': True}]}},
    },
}

_EXTENDS = {
    'anyof': [
        {'type': 'string'},
        {'type': 'dict', 'schema': {
            'service': {'type': 'string', 'required': True},
            'file':    {'type': 'string', 'required': False},
        }},
    ],
}

_CREDENTIAL_SPEC = {
    'type': 'dict', 'required': False, 'schema': {
        'config':   {'type': 'string', 'required': False},
        'file':     {'type': 'string', 'required': False},
        'registry': {'type': 'string', 'required': False},
    },
}

# ports element: short string ("80:80") or full dict.
_PORT_ELEMENT = {
    'anyof': [
        {'type': 'string'},
        {'type': 'integer'},
        {'type': 'dict', 'schema': {
            'name':         {'type': 'string',  'required': False},
            'target':       {'anyof': [{'type': 'integer'}, {'type': 'string'}], 'required': False},
            'published':    {'anyof': [{'type': 'integer'}, {'type': 'string'}], 'required': False},
            'host_ip':      {'type': 'string',  'required': False},
            'protocol':     {'type': 'string',  'required': False, 'allowed': ['tcp', 'udp']},
            'mode':         {'type': 'string',  'required': False, 'allowed': ['host', 'ingress']},
            'app_protocol': {'type': 'string',  'required': False},
        }},
    ],
}

# volume mount element: short string ("./data:/data:ro") or full dict.
_VOLUME_MOUNT_ELEMENT = {
    'anyof': [
        {'type': 'string'},
        {'type': 'dict', 'schema': {
            'type':        {'type': 'string',  'required': False, 'allowed': ['bind', 'volume', 'tmpfs', 'npipe', 'cluster', 'image']},
            'source':      {'type': 'string',  'required': False},
            'target':      {'type': 'string',  'required': True},
            'read_only':   {'type': 'boolean', 'required': False},
            'consistency': {'type': 'string',  'required': False},
            'bind':        {'type': 'dict',    'required': False, 'schema': {
                'propagation':        {'type': 'string',  'required': False},
                'create_host_path':   {'type': 'boolean', 'required': False},
                'selinux':            {'type': 'string',  'required': False, 'allowed': ['z', 'Z']},
            }},
            'volume':      {'type': 'dict',    'required': False, 'schema': {
                'nocopy':  {'type': 'boolean', 'required': False},
                'subpath': {'type': 'string',  'required': False},
            }},
            'tmpfs':       {'type': 'dict',    'required': False, 'schema': {
                'size': {'anyof': [{'type': 'integer'}, {'type': 'string'}], 'required': False},
                'mode': {'type': 'integer', 'required': False},
            }},
        }},
    ],
}

# networks attached to a service: list of names, OR dict of name -> opts.
_SERVICE_NETWORKS = {
    'anyof': [
        {'type': 'list', 'schema': {'type': 'string'}},
        {'type': 'dict', 'keysrules': {'type': 'string'}, 'valuesrules': {
            'nullable': True,
            'type': 'dict', 'schema': {
                'aliases':         {'type': 'list',    'required': False, 'schema': {'type': 'string'}},
                'ipv4_address':    {'type': 'string',  'required': False},
                'ipv6_address':    {'type': 'string',  'required': False},
                'link_local_ips':  {'type': 'list',    'required': False, 'schema': {'type': 'string'}},
                'mac_address':     {'type': 'string',  'required': False},
                'priority':        {'type': 'integer', 'required': False},
                'driver_opts':     {'type': 'dict',    'required': False},
                'gw_priority':     {'type': 'integer', 'required': False},
                'interface_name':  {'type': 'string',  'required': False},
            },
        }},
    ],
}

# configs / secrets attached to a service: list of names, or list of dicts.
_ATTACHED_CONFIG_OR_SECRET = {
    'type': 'list', 'required': False, 'schema': {
        'anyof': [
            {'type': 'string'},
            {'type': 'dict', 'schema': {
                'source': {'type': 'string',  'required': True},
                'target': {'type': 'string',  'required': False},
                'uid':    {'type': 'string',  'required': False},
                'gid':    {'type': 'string',  'required': False},
                'mode':   {'anyof': [{'type': 'integer'}, {'type': 'string'}], 'required': False},
            }},
        ],
    },
}

# env_file: string, list of strings, or list of {path, required, format}.
_ENV_FILE = {
    'anyof': [
        {'type': 'string'},
        {'type': 'list', 'schema': {
            'anyof': [
                {'type': 'string'},
                {'type': 'dict', 'schema': {
                    'path':     {'type': 'string',  'required': True},
                    'required': {'type': 'boolean', 'required': False},
                    'format':   {'type': 'string',  'required': False},
                }},
            ],
        }},
    ],
}

_ULIMIT_ELEMENT = {
    'anyof': [
        {'type': 'integer'},
        {'type': 'dict', 'schema': {
            'soft': {'type': 'integer', 'required': True},
            'hard': {'type': 'integer', 'required': True},
        }},
    ],
}

# ---------------------------------------------------------------------------
# Service body schema. Wired into the top-level PODMAN_COMPOSE_SCHEMA below
# as `services.<name>` body.
# ---------------------------------------------------------------------------

_PODMAN_COMPOSE_SERVICE_BODY = {
    'annotations':         _LIST_OR_DICT_OF_STRINGS,
    'attach':              {'type': 'boolean', 'required': False},
    'blkio_config':        {'type': 'dict',    'required': False},
    'build':               {**_BUILD, 'required': False},
    'cap_add':             {'type': 'list',    'required': False, 'schema': {'type': 'string'}},
    'cap_drop':            {'type': 'list',    'required': False, 'schema': {'type': 'string'}},
    'cgroup':              {'type': 'string',  'required': False, 'allowed': ['host', 'private']},
    'cgroup_parent':       {'type': 'string',  'required': False},
    'command':             {**_STRING_OR_LIST_OF_STRINGS, 'required': False},
    'configs':             _ATTACHED_CONFIG_OR_SECRET,
    'container_name':      {'type': 'string',  'required': False},
    'cpu_count':           {'type': 'integer', 'required': False},
    'cpu_percent':         {'type': 'integer', 'required': False},
    'cpu_period':          {'anyof': [{'type': 'integer'}, {'type': 'string'}], 'required': False},
    'cpu_quota':           {'anyof': [{'type': 'integer'}, {'type': 'string'}], 'required': False},
    'cpu_rt_period':       {'anyof': [{'type': 'integer'}, {'type': 'string'}], 'required': False},
    'cpu_rt_runtime':      {'anyof': [{'type': 'integer'}, {'type': 'string'}], 'required': False},
    'cpu_shares':          {'type': 'integer', 'required': False},
    'cpus':                {'anyof': [{'type': 'number'}, {'type': 'string'}], 'required': False},
    'cpuset':              {'type': 'string',  'required': False},
    'credential_spec':     _CREDENTIAL_SPEC,
    'depends_on':          {**_DEPENDS_ON, 'required': False},
    'deploy':              _DEPLOY,
    'develop':             {'type': 'dict',    'required': False},
    'device_cgroup_rules': {'type': 'list',    'required': False, 'schema': {'type': 'string'}},
    'devices':             {'type': 'list',    'required': False},
    'dns':                 {**_STRING_OR_LIST_OF_STRINGS, 'required': False},
    'dns_opt':             {'type': 'list',    'required': False, 'schema': {'type': 'string'}},
    'dns_search':          {**_STRING_OR_LIST_OF_STRINGS, 'required': False},
    'domainname':          {'type': 'string',  'required': False},
    'entrypoint':          {**_STRING_OR_LIST_OF_STRINGS, 'required': False},
    'env_file':            {**_ENV_FILE,       'required': False},
    'environment':         _ENVIRONMENT,
    'expose':              {'type': 'list',    'required': False, 'schema': {'anyof': [{'type': 'string'}, {'type': 'integer'}]}},
    'extends':             {**_EXTENDS,        'required': False},
    'external_links':      {'type': 'list',    'required': False, 'schema': {'type': 'string'}},
    'extra_hosts':         _LIST_OR_DICT_OF_STRINGS,
    'gpus':                {'anyof': [{'type': 'string'}, {'type': 'list'}], 'required': False},
    'group_add':           {'type': 'list',    'required': False, 'schema': {'anyof': [{'type': 'string'}, {'type': 'integer'}]}},
    'healthcheck':         _HEALTHCHECK,
    'hostname':            {'type': 'string',  'required': False},
    'image':               {'type': 'string',  'required': False},
    'init':                {'type': 'boolean', 'required': False},
    'ipc':                 {'type': 'string',  'required': False},
    'isolation':           {'type': 'string',  'required': False},
    'label_file':          {**_STRING_OR_LIST_OF_STRINGS, 'required': False},
    'labels':              _LIST_OR_DICT_OF_STRINGS,
    'links':               {'type': 'list',    'required': False, 'schema': {'type': 'string'}},
    'logging':             _LOGGING,
    'mac_address':         {'type': 'string',  'required': False},
    'mem_limit':           {'anyof': [{'type': 'string'}, {'type': 'integer'}], 'required': False},
    'mem_reservation':     {'anyof': [{'type': 'string'}, {'type': 'integer'}], 'required': False},
    'mem_swappiness':      {'type': 'integer', 'required': False},
    'memswap_limit':       {'anyof': [{'type': 'string'}, {'type': 'integer'}], 'required': False},
    'network_mode':        {'type': 'string',  'required': False},
    'networks':            {**_SERVICE_NETWORKS, 'required': False},
    'oom_kill_disable':    {'type': 'boolean', 'required': False},
    'oom_score_adj':       {'type': 'integer', 'required': False},
    'pid':                 {'type': 'string',  'required': False, 'nullable': True},
    'pids_limit':          {'anyof': [{'type': 'integer'}, {'type': 'string'}], 'required': False},
    'platform':            {'type': 'string',  'required': False},
    'ports':               {'type': 'list',    'required': False, 'schema': _PORT_ELEMENT},
    'post_start':          {'type': 'list',    'required': False},
    'pre_stop':            {'type': 'list',    'required': False},
    'privileged':          {'type': 'boolean', 'required': False},
    'profiles':            {'type': 'list',    'required': False, 'schema': {'type': 'string'}},
    'pull_policy':         {'type': 'string',  'required': False},
    'read_only':           {'type': 'boolean', 'required': False},
    'restart':             {'type': 'string',  'required': False, 'allowed': ['no', 'always', 'on-failure', 'unless-stopped']},
    'runtime':             {'type': 'string',  'required': False},
    'scale':               {'type': 'integer', 'required': False},
    'secrets':             _ATTACHED_CONFIG_OR_SECRET,
    'security_opt':        {'type': 'list',    'required': False, 'schema': {'type': 'string'}},
    'shm_size':            {'anyof': [{'type': 'string'}, {'type': 'integer'}], 'required': False},
    'stdin_open':          {'type': 'boolean', 'required': False},
    'stop_grace_period':   {'type': 'string',  'required': False},
    'stop_signal':         {'type': 'string',  'required': False},
    'storage_opt':         {'type': 'dict',    'required': False},
    'sysctls':             _LIST_OR_DICT_OF_STRINGS,
    'tmpfs':               {**_STRING_OR_LIST_OF_STRINGS, 'required': False},
    'tty':                 {'type': 'boolean', 'required': False},
    'ulimits':             {'type': 'dict',    'required': False, 'keysrules': {'type': 'string'}, 'valuesrules': _ULIMIT_ELEMENT},
    'user':                {'type': 'string',  'required': False},
    'userns_mode':         {'type': 'string',  'required': False},
    'uts':                 {'type': 'string',  'required': False},
    'volumes':             {'type': 'list',    'required': False, 'schema': _VOLUME_MOUNT_ELEMENT},
    'volumes_from':        {'type': 'list',    'required': False, 'schema': {'type': 'string'}},
    'working_dir':         {'type': 'string',  'required': False},
}

# ---------------------------------------------------------------------------
# Top-level network / volume / config / secret body schemas. Each top-level
# section is a dict keyed by name; the body is one of these.
# ---------------------------------------------------------------------------

_PODMAN_COMPOSE_NETWORK_BODY = {
    'name':          {'type': 'string',  'required': False},
    'driver':        {'type': 'string',  'required': False},
    'driver_opts':   {'type': 'dict',    'required': False, 'keysrules': {'type': 'string'}},
    'attachable':    {'type': 'boolean', 'required': False},
    'enable_ipv4':   {'type': 'boolean', 'required': False},
    'enable_ipv6':   {'type': 'boolean', 'required': False},
    'external':      {**_EXTERNAL_FLAG, 'required': False},
    'internal':      {'type': 'boolean', 'required': False},
    'ipam':          {'type': 'dict',    'required': False, 'schema': {
        'driver':  {'type': 'string', 'required': False},
        'config':  {'type': 'list',   'required': False, 'schema': {'type': 'dict', 'schema': {
            'subnet':        {'type': 'string', 'required': False},
            'ip_range':      {'type': 'string', 'required': False},
            'gateway':       {'type': 'string', 'required': False},
            'aux_addresses': {'type': 'dict',   'required': False},
        }}},
        'options': {'type': 'dict',   'required': False},
    }},
    'labels':        _LIST_OR_DICT_OF_STRINGS,
}

_PODMAN_COMPOSE_VOLUME_BODY = {
    'name':        {'type': 'string',  'required': False},
    'driver':      {'type': 'string',  'required': False},
    'driver_opts': {'type': 'dict',    'required': False, 'keysrules': {'type': 'string'}},
    'external':    {**_EXTERNAL_FLAG,  'required': False},
    'labels':      _LIST_OR_DICT_OF_STRINGS,
}

_PODMAN_COMPOSE_CONFIG_BODY = {
    'name':            {'type': 'string',  'required': False},
    'file':            {'type': 'string',  'required': False},
    'environment':     {'type': 'string',  'required': False},
    'content':         {'type': 'string',  'required': False},
    'external':        {**_EXTERNAL_FLAG,  'required': False},
    'labels':          _LIST_OR_DICT_OF_STRINGS,
    'template_driver': {'type': 'string',  'required': False},
}

_PODMAN_COMPOSE_SECRET_BODY = {
    'name':            {'type': 'string',  'required': False},
    'file':            {'type': 'string',  'required': False},
    'environment':     {'type': 'string',  'required': False},
    'external':        {**_EXTERNAL_FLAG,  'required': False},
    'labels':          _LIST_OR_DICT_OF_STRINGS,
    'driver':          {'type': 'string',  'required': False},
    'driver_opts':     {'type': 'dict',    'required': False, 'keysrules': {'type': 'string'}},
    'template_driver': {'type': 'string',  'required': False},
}

# ---------------------------------------------------------------------------
# Top-level compose schema. Validates a whole compose document.
# ---------------------------------------------------------------------------

PODMAN_COMPOSE_SCHEMA = {
    'version':  {'type': 'string', 'required': False},
    'name':     {'type': 'string', 'required': False},
    'services': {
        'type': 'dict', 'required': False,
        'keysrules':   {'type': 'string'},
        'valuesrules': {'type': 'dict', 'schema': _PODMAN_COMPOSE_SERVICE_BODY, 'nullable': False},
    },
    'networks': {
        'type': 'dict', 'required': False,
        'keysrules':   {'type': 'string'},
        'valuesrules': {'type': 'dict', 'schema': _PODMAN_COMPOSE_NETWORK_BODY, 'nullable': True},
    },
    'volumes':  {
        'type': 'dict', 'required': False,
        'keysrules':   {'type': 'string'},
        'valuesrules': {'type': 'dict', 'schema': _PODMAN_COMPOSE_VOLUME_BODY, 'nullable': True},
    },
    'configs':  {
        'type': 'dict', 'required': False,
        'keysrules':   {'type': 'string'},
        'valuesrules': {'type': 'dict', 'schema': _PODMAN_COMPOSE_CONFIG_BODY, 'nullable': True},
    },
    'secrets':  {
        'type': 'dict', 'required': False,
        'keysrules':   {'type': 'string'},
        'valuesrules': {'type': 'dict', 'schema': _PODMAN_COMPOSE_SECRET_BODY, 'nullable': True},
    },
}

#############################################################################
#############################################################################
# END PODMAN COMPOSE
#############################################################################
#############################################################################


#############################################################################
#############################################################################
# BEGIN KUBERNETES
#############################################################################
#############################################################################

# ---------------------------------------------------------------------------
# Kubernetes rule-set — single-document scope: the root is one kubectl-legal
# `apiVersion: v1 / kind: List / items: [...]` document. Multi-document
# `---` streams are the deferred loader trigger (see
# ./check_out/IAC_GRAMMAR_SURVEY.md, trigger #2) and fail at the
# single-document YAML loader, by design.
#
# Kind discrimination is encoded in-schema (survey recommendation #6): the
# per-item `oneof` pins `kind` per branch, so exactly one item schema can
# match. Kinds in scope: Namespace, Pod, Service. Hand-authored inputs
# only — kubectl-export bookkeeping fields (status, resourceVersion,
# creationTimestamp, ...) are not accepted.
# ---------------------------------------------------------------------------

_K8S_STRING_DICT = {
    'type': 'dict', 'required': False,
    'keysrules': {'type': 'string'}, 'valuesrules': {'type': 'string'},
}

# metadata block shared by all kinds. `namespace` is meaningful for Pod and
# Service; a Namespace object carries only its own cluster-unique `name`.
_K8S_METADATA = {
    'type': 'dict', 'required': True, 'schema': {
        'name':        {'type': 'string', 'required': True},
        'namespace':   {'type': 'string', 'required': False},
        'labels':      _K8S_STRING_DICT,
        'annotations': {'type': 'dict',   'required': False},  # free-form; `launcher/*` keys are read by Builders.py
    },
}

# container port: `-p` is emitted by the builder only for entries carrying
# hostPort (K8s hostPort semantics — containerPort alone publishes nothing).
_K8S_CONTAINER_PORT = {
    'containerPort': {'type': 'integer', 'required': True},
    'hostPort':      {'type': 'integer', 'required': False},
    'protocol':      {'type': 'string',  'required': False, 'allowed': ['TCP', 'UDP']},
    'name':          {'type': 'string',  'required': False},
}

# env entry: K8s stringifies values at runtime; YAML parses unquoted
# numerics as int, so both are allowed (same leniency as compose).
_K8S_ENV_VAR = {
    'name':  {'type': 'string', 'required': True},
    'value': {'anyof': [{'type': 'string'}, {'type': 'integer'}], 'required': False},
}

_K8S_CONTAINER = {
    'name':            {'type': 'string', 'required': True},
    'image':           {'type': 'string', 'required': True},
    'ports':           {'type': 'list',   'required': False, 'schema': {'type': 'dict', 'schema': _K8S_CONTAINER_PORT}},
    'env':             {'type': 'list',   'required': False, 'schema': {'type': 'dict', 'schema': _K8S_ENV_VAR}},
    'command':         {'type': 'list',   'required': False, 'schema': {'type': 'string'}},
    'args':            {'type': 'list',   'required': False, 'schema': {'type': 'string'}},
    'workingDir':      {'type': 'string', 'required': False},
    'imagePullPolicy': {'type': 'string', 'required': False, 'allowed': ['Always', 'IfNotPresent', 'Never']},  # accepted, unused
}

# ---------------------------------------------------------------------------
# Per-kind item schemas. Each pins `kind` to a single allowed value — that
# is the discriminator the root `oneof` branches on.
# ---------------------------------------------------------------------------

_K8S_NAMESPACE_ITEM = {
    'apiVersion': {'type': 'string', 'required': True, 'allowed': ['v1']},
    'kind':       {'type': 'string', 'required': True, 'allowed': ['Namespace']},
    'metadata':   _K8S_METADATA,
}

_K8S_POD_ITEM = {
    'apiVersion': {'type': 'string', 'required': True, 'allowed': ['v1']},
    'kind':       {'type': 'string', 'required': True, 'allowed': ['Pod']},
    'metadata':   _K8S_METADATA,
    'spec':       {'type': 'dict', 'required': True, 'schema': {
        'containers': {'type': 'list', 'required': True, 'minlength': 1,
                       'schema': {'type': 'dict', 'schema': _K8S_CONTAINER}},
    }},
}

_K8S_SERVICE_ITEM = {
    'apiVersion': {'type': 'string', 'required': True, 'allowed': ['v1']},
    'kind':       {'type': 'string', 'required': True, 'allowed': ['Service']},
    'metadata':   _K8S_METADATA,
    'spec':       {'type': 'dict', 'required': True, 'schema': {
        'selector': {'type': 'dict', 'required': True,
                     'keysrules': {'type': 'string'}, 'valuesrules': {'type': 'string'}},
        'ports':    {'type': 'list',   'required': False},  # accepted, unused — no VIP semantics
        'type':     {'type': 'string', 'required': False},  # accepted, unused
    }},
}

# ---------------------------------------------------------------------------
# Top-level kubernetes schema. Validates one `kind: List` document.
# ---------------------------------------------------------------------------

KUBERNETES_SCHEMA = {
    'apiVersion': {'type': 'string', 'required': True, 'allowed': ['v1']},
    'kind':       {'type': 'string', 'required': True, 'allowed': ['List']},
    'items':      {'type': 'list',   'required': True, 'schema': {
        'type': 'dict', 'oneof': [
            {'schema': _K8S_NAMESPACE_ITEM},
            {'schema': _K8S_POD_ITEM},
            {'schema': _K8S_SERVICE_ITEM},
        ],
    }},
}

#############################################################################
#############################################################################
# END KUBERNETES
#############################################################################
#############################################################################
