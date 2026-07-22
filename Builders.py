from __future__ import annotations
from dataclasses import dataclass, field
from BlueprintProcessor import (
    Blueprint,
    MAPPING_SIDE_AFTER,
    make_mapping,
    reconcile_mappings,
    validate_blueprint_dag,
    draw_unified_graph,
)
from UnitFileCreator import Unit_File


#############################################################################
# Builders — the semantic halves of the rule-sets (one section per DSL:
# podman-compose, then kubernetes).
#
# Each section holds only its DSL-specific knowledge: the extended Blueprint
# class (its optional fields plus their unit-file treatment), the keyword →
# mapping-side map, the discriminator / unit-type constants, and the
# per-instance construction functions. Everything that reads only the
# Blueprint superclass contract lives in
# ./src/UnitGenerator/BlueprintProcessor.py.
#
# The pipeline running order is declared by the RuleSet fields in ./Rules.py;
# this module contributes the per-DSL `blueprint_builder` step (P2-P4):
# `podman_compose_blueprint_builder` and `kubernetes_blueprint_builder`.
#############################################################################


#############################################################################
#############################################################################
# BEGIN PODMAN COMPOSE
#############################################################################
#############################################################################


#############################################################################
# Compose-specific constants.
#############################################################################

# Type strings are the discriminator values the manifest dumper writes into
# the `type=` field of each INSTANCE block.
PODMAN_COMPOSE_SERVICE_TYPE = 'SERVICE'
PODMAN_COMPOSE_NETWORK_TYPE = 'NETWORK'

# Compose-specific keyword → side map. The keys here are exactly the
# mapping names that will appear in `BlueprintPodmanCompose.mapping_dict`.
#   `depends_on` → AFTER : the listed services must be up first.
#   `networks`   → AFTER : the network must exist before a service joins.
_PODMAN_COMPOSE_KEYWORD_SIDES = {
    'depends_on': MAPPING_SIDE_AFTER,
    'networks':   MAPPING_SIDE_AFTER,
}

# ---------------------------------------------------------------------------
# Compose-specific systemd unit settings. Pre-set in the Blueprint by
# _build_service / _build_network so the unit-file emitter stays generic.
#
# Type=notify        : long-running service (default for SERVICE). Paired
#                      with `podman run --sdnotify=conmon` so the start job
#                      completes only when conmon signals READY, i.e. the
#                      container is actually created and running — not when
#                      ExecStart merely forks.
# Type=oneshot       : short-lived; combined with RemainAfterExit=yes for
#                      NETWORK so the unit stays "active" after the network
#                      create command exits.
# ---------------------------------------------------------------------------

_PODMAN_COMPOSE_SERVICE_UNIT_TYPE = 'notify'
_PODMAN_COMPOSE_NETWORK_UNIT_TYPE = 'oneshot'


#############################################################################
# BlueprintPodmanCompose — per-instance Blueprint for the podman-compose
# rule-set. One Blueprint per service or network.
#############################################################################

@dataclass(kw_only=True)
class BlueprintPodmanCompose(Blueprint):
    """
    Podman-compose Blueprint. Inherits the cross-DSL contract from
    `Blueprint` and adds the compose-specific fields the builders need,
    plus the `apply_unit_file_settings` override that writes them into
    the unit file.

    `mapping_dict` is populated for 'depends_on' and 'networks' today.
    New compose mappings (volumes, configs, secrets, …) drop in via the
    `mapping_dict` key without touching this class.

    Compose-specific fields:
        container_name  : value for `podman run --name <CN>`; the unit-file
                          emitter's ExecStop / ExecStopPost reference it as
                          well. Empty default — the builder fills it from
                          YAML's `container_name` when set; left empty
                          otherwise (the run command omits `--name`).
        stop_command    : ExecStop= line value.
                          Service: `podman stop <container_name>`.
                          Network: `podman network rm <name>`.
                          Empty when no clean stop is possible.
        cleanup_command : ExecStopPost= line value.
                          Service: `podman rm <container_name>`.
                          Network: empty (no post-stop action).
                          The unit-file emitter omits the ExecStopPost
                          line when this is empty.
    """
    container_name    : str  = ''
    stop_command      : str  = ''
    cleanup_command   : str  = ''
    unit_type         : str  = ''     # systemd [Service] Type= (e.g. 'notify', 'oneshot')
    remain_after_exit : bool = False  # systemd [Service] RemainAfterExit=yes when True

    def apply_unit_file_settings(self, unit_file: Unit_File) -> None:
        """
        Write the unit-file lines that come from the compose-specific
        fields. Called by the generic emitter right before the dump.
        """
        if self.unit_type:
            unit_file.edit_field('SERVICE', 'Type', self.unit_type)
        if self.unit_type == 'notify':
            # READY arrives from conmon, a child of the main podman process.
            unit_file.edit_field('SERVICE', 'NotifyAccess', 'all')
        if self.remain_after_exit:
            unit_file.edit_field('SERVICE', 'RemainAfterExit', 'yes')
        if self.stop_command:
            unit_file.edit_field('SERVICE', 'ExecStop', self.stop_command)
        if self.cleanup_command:
            unit_file.edit_field('SERVICE', 'ExecStopPost', self.cleanup_command)


#############################################################################
# podman_compose_blueprint_builder — steps P2-P4 of the pipeline for the
# podman-compose rule-set. Bound into `RuleSet.blueprint_builder` by
# ./Rules.py.
#############################################################################

def podman_compose_blueprint_builder(
    validated_input          : dict,
    failure_behavior_default : str,
    mapping_behavior_default : str,
) -> list[BlueprintPodmanCompose]:
    """
    Steps 2 + 3 + 4: build one BlueprintPodmanCompose per service and per
    network, reconcile each mapping's `before` lists from others' `after`
    lists, then DAG-validate the unified after-graph. Reconciliation, DAG
    validation, and drawing are the generic helpers from BlueprintProcessor.

    Iterates `validated_input['services']` and `validated_input['networks']`
    only — top-level keys `version`, `name`, `volumes`, `configs`, `secrets`
    are out of scope for instance enumeration today.

    Bodies may be None (compose-spec allows empty section bodies like
    `networks: { frontend: }`); normalised to `{}` here.
    """
    services = validated_input.get('services') or {}
    networks = validated_input.get('networks') or {}

    blueprints = [
        _build_service(name, body or {}, failure_behavior_default, mapping_behavior_default)
        for name, body in services.items()
    ] + [
        _build_network(name, body or {}, failure_behavior_default, mapping_behavior_default)
        for name, body in networks.items()
    ]

    reconcile_mappings(blueprints)
    unified_after = validate_blueprint_dag(blueprints)
    draw_unified_graph(unified_after)
    return blueprints


#############################################################################
# Private helpers — per-instance Blueprint construction (step 2).
#############################################################################

def _make_compose_mapping(name: str, values: list[str]):
    """
    Build a `Mapping` for one compose keyword, placing the listed values
    on the side the keyword imposes (per `_PODMAN_COMPOSE_KEYWORD_SIDES`).
    """
    return make_mapping(name, values, _PODMAN_COMPOSE_KEYWORD_SIDES[name])


def _build_service(
    name                     : str,
    body                     : dict,
    failure_behavior_default : str,
    mapping_behavior_default : str,
) -> BlueprintPodmanCompose:
    """Build one Service Blueprint from a validated compose service body."""
    container_name = body.get('container_name', '')

    command         = _build_podman_run_command(body)
    stop_command    = f'podman stop {container_name}' if container_name else ''
    cleanup_command = f'podman rm {container_name}'   if container_name else ''

    mapping_dict = {
        'depends_on': _make_compose_mapping('depends_on', _extract_depends_on(body.get('depends_on', []))),
        'networks':   _make_compose_mapping('networks',   _extract_networks  (body.get('networks',   []))),
    }

    return BlueprintPodmanCompose(
        name              = name,
        unit_file_name    = f'_launcher_{name}.service',
        type_             = PODMAN_COMPOSE_SERVICE_TYPE,
        path              = '',
        command           = command,
        container_name    = container_name,
        stop_command      = stop_command,
        cleanup_command   = cleanup_command,
        unit_type         = _PODMAN_COMPOSE_SERVICE_UNIT_TYPE,
        remain_after_exit = False,
        failure_behavior  = failure_behavior_default,
        mapping_behavior  = mapping_behavior_default,
        mapping_count     = len(mapping_dict),
        mapping_dict      = mapping_dict,
    )


def _build_network(
    name                     : str,
    body                     : dict,
    failure_behavior_default : str,
    mapping_behavior_default : str,
) -> BlueprintPodmanCompose:
    """
    Build one Network Blueprint. Networks emit `.service` units that run
    `podman network create`. They have no depends_on / networks of their
    own in compose-spec; both mappings present-but-empty so the dict shape
    is uniform across Service and Network Blueprints.
    """
    mapping_dict = {
        'depends_on': _make_compose_mapping('depends_on', []),
        'networks':   _make_compose_mapping('networks',   []),
    }

    return BlueprintPodmanCompose(
        name              = name,
        unit_file_name    = f'_launcher_{name}.service',
        type_             = PODMAN_COMPOSE_NETWORK_TYPE,
        path              = '',
        command           = f'podman network create --ignore {name}',
        container_name    = '',
        stop_command      = f'podman network rm {name}',
        cleanup_command   = '',
        unit_type         = _PODMAN_COMPOSE_NETWORK_UNIT_TYPE,
        remain_after_exit = True,
        failure_behavior  = failure_behavior_default,
        mapping_behavior  = mapping_behavior_default,
        mapping_count     = len(mapping_dict),
        mapping_dict      = mapping_dict,
    )


def _extract_depends_on(value) -> list[str]:
    """Compose `depends_on` is either a list of names or a dict {name: {...}}."""
    if isinstance(value, dict):
        return list(value.keys())
    return list(value)


def _extract_networks(value) -> list[str]:
    """Compose service `networks` is either a list of names or a dict {name: {...}}."""
    if isinstance(value, dict):
        return list(value.keys())
    return list(value)


def _build_podman_run_command(body: dict) -> str:
    """
    Build the `podman run …` command string from a compose service body.

    Coverage matches the old source's `_build_podman_run_command`:
    image, container_name, networks (list of names), ports (short-form
    strings), volumes (short-form strings), environment (dict only),
    entrypoint (string or list), working_dir, command (string or list).
    Long-form ports/volumes and list-form environment are skipped here —
    expand when a real case needs them.
    """
    parts = ['podman run', '--sdnotify=conmon']

    container_name = body.get('container_name', '')
    if container_name:
        parts.append(f'--name {container_name}')

    for net in _extract_networks(body.get('networks', [])):
        parts.append(f'--network {net}')

    for port in body.get('ports', []):
        if isinstance(port, str):
            parts.append(f'-p {port}')

    for vol in body.get('volumes', []):
        if isinstance(vol, str):
            parts.append(f'-v {vol}')

    env = body.get('environment', {})
    if isinstance(env, dict):
        for k, v in env.items():
            parts.append(f'-e {k}={v}')

    entrypoint = body.get('entrypoint', '')
    if isinstance(entrypoint, list):
        entrypoint = ' '.join(entrypoint)
    if entrypoint:
        parts.append(f'--entrypoint {entrypoint}')

    working_dir = body.get('working_dir', '')
    if working_dir:
        parts.append(f'--workdir {working_dir}')

    image = body.get('image', '')
    if image:
        parts.append(image)

    command = body.get('command', '')
    if isinstance(command, list):
        command = ' '.join(command)
    if command:
        parts.append(command)

    return ' '.join(parts)


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
# Semantic half of the kubernetes rule-set. Single-document `kind: List`
# scope (see Schemas.py). Instance-hood is decided here and materialised at
# unit-file emission:
#
#   Namespace → group abstraction, `podman network create` oneshot unit
#               (identical materialization to compose networks).
#   Pod       → executable, `podman run` notify unit (single-container
#               scope — spec.containers[0]).
#   Service   → group abstraction, no-exec sync-point oneshot unit ordered
#               after the pods its label selector matches. Selector
#               resolution happens here at build time — reference
#               resolution is rule-set work, never generic.
#
# Pod metadata.annotations carry the two relations K8s has no keyword for:
#   launcher/depends-on : pod → pod start ordering (compose depends_on
#                         analog); space-separated names, `other-ns/name`
#                         crosses namespaces.
#   launcher/networks   : network attachments beyond the pod's namespace
#                         (compose multi-network analog).
#############################################################################

# Type strings written into the manifest's `type=` field.
KUBERNETES_NAMESPACE_TYPE = 'NAMESPACE'
KUBERNETES_POD_TYPE       = 'POD'
KUBERNETES_SERVICE_TYPE   = 'SERVICE'

# Keyword → side map. All kubernetes mappings impose the AFTER side:
#   `namespace`  → AFTER : the namespace's network must exist before a pod
#                          or service that lives in it starts. A pod's
#                          values list carries its identity namespace plus
#                          any `launcher/networks` extras — the full set of
#                          network attachments, like compose's `networks`.
#   `selector`   → AFTER : a service is a sync-point that completes after
#                          the pods its label selector matches are up.
#   `depends_on` → AFTER : pod → pod start ordering, read from the
#                          `launcher/depends-on` annotation. Kubernetes has
#                          no native start-ordering keyword, so the dialect
#                          carries the relation in metadata.annotations
#                          (kubectl-legal; a real cluster ignores it). Same
#                          mapping name and role as compose's depends_on.
_KUBERNETES_KEYWORD_SIDES = {
    'namespace':  MAPPING_SIDE_AFTER,
    'selector':   MAPPING_SIDE_AFTER,
    'depends_on': MAPPING_SIDE_AFTER,
}

# Annotation keys interpreted by this rule-set. Annotation values are
# strings in K8s, so multiple names are space-separated. A name in
# `launcher/depends-on` resolves inside the pod's own namespace; an
# `other-ns/name` token crosses namespaces.
_K8S_ANNOTATION_DEPENDS_ON = 'launcher/depends-on'
_K8S_ANNOTATION_NETWORKS   = 'launcher/networks'

# systemd unit settings per kind. Pods pair Type=notify with
# `podman run --sdnotify=conmon` (same rationale as compose services).
# Namespace and Service units are short-lived groups: Type=oneshot +
# RemainAfterExit=yes keeps them "active" after their command exits.
_KUBERNETES_POD_UNIT_TYPE   = 'notify'
_KUBERNETES_GROUP_UNIT_TYPE = 'oneshot'

# Sync-point command for Service units — they have no executable of their
# own; the unit exists to aggregate ordering over the selected pods.
_KUBERNETES_SERVICE_COMMAND = '/bin/true'


#############################################################################
# BlueprintKubernetes — per-instance Blueprint for the kubernetes rule-set.
# One Blueprint per Namespace, Pod, or Service item.
#############################################################################

@dataclass(kw_only=True)
class BlueprintKubernetes(Blueprint):
    """
    Kubernetes Blueprint. Inherits the cross-DSL contract from `Blueprint`
    and adds the kubernetes-specific fields the builders need, plus the
    `apply_unit_file_settings` override that writes them into the unit
    file.

    `mapping_dict` is populated for 'namespace', 'selector' and
    'depends_on' today.

    Kubernetes-specific fields:
        namespace         : metadata.namespace for Pod / Service instances
                            (with the K8s 'default' fallback applied);
                            empty for Namespace instances themselves.
        labels            : metadata.labels — the selector-matching input;
                            kept on the Blueprint so the resolved 'selector'
                            mapping stays inspectable after the build.
        container_name    : `podman run --name` value ('<ns>_<pod>');
                            empty for Namespace / Service instances.
        stop_command      : ExecStop= line value.
                            Pod: `podman stop <container_name>`.
                            Namespace: `podman network rm <name>`.
                            Service: empty (nothing to stop).
        cleanup_command   : ExecStopPost= line value.
                            Pod: `podman rm <container_name>`; else empty.
    """
    namespace         : str            = ''
    labels            : dict[str, str] = field(default_factory=dict)
    container_name    : str            = ''
    stop_command      : str            = ''
    cleanup_command   : str            = ''
    unit_type         : str            = ''     # systemd [Service] Type= (e.g. 'notify', 'oneshot')
    remain_after_exit : bool           = False  # systemd [Service] RemainAfterExit=yes when True

    def apply_unit_file_settings(self, unit_file: Unit_File) -> None:
        """
        Write the unit-file lines that come from the kubernetes-specific
        fields. Called by the generic emitter right before the dump.
        """
        if self.unit_type:
            unit_file.edit_field('SERVICE', 'Type', self.unit_type)
        if self.unit_type == 'notify':
            # READY arrives from conmon, a child of the main podman process.
            unit_file.edit_field('SERVICE', 'NotifyAccess', 'all')
        if self.remain_after_exit:
            unit_file.edit_field('SERVICE', 'RemainAfterExit', 'yes')
        if self.stop_command:
            unit_file.edit_field('SERVICE', 'ExecStop', self.stop_command)
        if self.cleanup_command:
            unit_file.edit_field('SERVICE', 'ExecStopPost', self.cleanup_command)


#############################################################################
# kubernetes_blueprint_builder — steps P2-P4 of the pipeline for the
# kubernetes rule-set. Bound into `RuleSet.blueprint_builder` by ./Rules.py.
#############################################################################

def kubernetes_blueprint_builder(
    validated_input          : dict,
    failure_behavior_default : str,
    mapping_behavior_default : str,
) -> list[BlueprintKubernetes]:
    """
    Steps 2 + 3 + 4: build one BlueprintKubernetes per Namespace / Pod /
    Service item, then run the generic reconcile / DAG-validate / draw tail
    from BlueprintProcessor.

    Pass 1 partitions `items` by kind and collects the declared namespace
    names; pass 2 builds the Blueprints. The split exists because
    (a) `namespace` edges are emitted only toward declared Namespace
    instances — the C-side manifest reader rejects unresolved peer names,
    and a pod in an undeclared namespace (e.g. the implicit 'default')
    must not produce a dangling edge — and (b) each Service's label
    selector is resolved against the pod inventory at build time.

    The 'selector' and 'namespace' strata (service → pod → namespace) are
    acyclic by construction. 'depends_on' edges are user-authored pod → pod
    relations, so the generic DAG validator is load-bearing for them, not a
    formality.
    """
    items = validated_input.get('items') or []

    namespaces = [item for item in items if item['kind'] == 'Namespace']
    pods       = [item for item in items if item['kind'] == 'Pod']
    services   = [item for item in items if item['kind'] == 'Service']

    declared_namespaces = {ns['metadata']['name'] for ns in namespaces}

    blueprints = [
        _build_k8s_namespace(item, failure_behavior_default, mapping_behavior_default)
        for item in namespaces
    ] + [
        _build_k8s_pod(item, declared_namespaces, failure_behavior_default, mapping_behavior_default)
        for item in pods
    ] + [
        _build_k8s_service(item, pods, declared_namespaces, failure_behavior_default, mapping_behavior_default)
        for item in services
    ]

    reconcile_mappings(blueprints)
    unified_after = validate_blueprint_dag(blueprints)
    draw_unified_graph(unified_after)
    return blueprints


#############################################################################
# Private helpers — per-instance Blueprint construction (step 2).
#############################################################################

def _make_k8s_mapping(name: str, values: list[str]):
    """
    Build a `Mapping` for one kubernetes keyword, placing the listed values
    on the side the keyword imposes (per `_KUBERNETES_KEYWORD_SIDES`).
    """
    return make_mapping(name, values, _KUBERNETES_KEYWORD_SIDES[name])


def _k8s_instance_namespace(item: dict) -> str:
    """metadata.namespace with the K8s 'default' fallback applied."""
    return item['metadata'].get('namespace', '') or 'default'


def _k8s_instance_name(item: dict) -> str:
    """
    Launcher instance name for one item. Pod / Service names are unique
    only per (namespace, kind) in K8s, so their instance names are
    namespace-prefixed; Namespace objects use their bare cluster-unique
    name.
    """
    if item['kind'] == 'Namespace':
        return item['metadata']['name']
    return f"{_k8s_instance_namespace(item)}_{item['metadata']['name']}"


def _k8s_annotation_list(item: dict, key: str) -> list[str]:
    """
    Space-separated token list of one `launcher/*` annotation; empty when
    the annotation is absent.
    """
    annotations = item['metadata'].get('annotations') or {}
    return str(annotations.get(key) or '').split()


def _match_k8s_selector(selector: dict, pod_item: dict) -> bool:
    """
    Equality-based selector match: every selector pair must appear
    verbatim in the pod's labels (labels ⊇ selector). `matchExpressions`
    are out of scope.
    """
    labels = pod_item['metadata'].get('labels') or {}
    return all(labels.get(key) == value for key, value in selector.items())


def _build_k8s_namespace(
    item                     : dict,
    failure_behavior_default : str,
    mapping_behavior_default : str,
) -> BlueprintKubernetes:
    """
    Build one Namespace Blueprint — a group abstraction materialised as a
    `.service` unit that runs `podman network create` (the compose-network
    analogue). Both mappings present-but-empty so the dict shape is
    uniform across all kubernetes Blueprints.
    """
    name = item['metadata']['name']

    mapping_dict = {
        'namespace':  _make_k8s_mapping('namespace',  []),
        'selector':   _make_k8s_mapping('selector',   []),
        'depends_on': _make_k8s_mapping('depends_on', []),
    }

    return BlueprintKubernetes(
        name              = name,
        unit_file_name    = f'_launcher_{name}.service',
        type_             = KUBERNETES_NAMESPACE_TYPE,
        path              = '',
        command           = f'podman network create --ignore {name}',
        namespace         = '',
        labels            = dict(item['metadata'].get('labels') or {}),
        container_name    = '',
        stop_command      = f'podman network rm {name}',
        cleanup_command   = '',
        unit_type         = _KUBERNETES_GROUP_UNIT_TYPE,
        remain_after_exit = True,
        failure_behavior  = failure_behavior_default,
        mapping_behavior  = mapping_behavior_default,
        mapping_count     = len(mapping_dict),
        mapping_dict      = mapping_dict,
    )


def _build_k8s_pod(
    item                     : dict,
    declared_namespaces      : set[str],
    failure_behavior_default : str,
    mapping_behavior_default : str,
) -> BlueprintKubernetes:
    """
    Build one Pod Blueprint from a validated Pod item. The identity
    namespace contributes its `namespace` mapping edge (and `--network`
    flag) only when declared as a Namespace item in the same input — the
    implicit 'default' must not produce a dangling edge. Annotation-borne
    relations (`launcher/networks` extras, `launcher/depends-on` names)
    are explicit user intent and are emitted verbatim: a dangling name
    fails loudly at the C-side manifest resolver, the same contract as
    compose's depends_on / networks.
    """
    ns             = _k8s_instance_namespace(item)
    name           = _k8s_instance_name(item)
    in_declared_ns = ns in declared_namespaces

    networks = ([ns] if in_declared_ns else []) \
             + _k8s_annotation_list(item, _K8S_ANNOTATION_NETWORKS)

    depends_on = [
        token.replace('/', '_', 1) if '/' in token else f'{ns}_{token}'
        for token in _k8s_annotation_list(item, _K8S_ANNOTATION_DEPENDS_ON)
    ]

    command = _build_podman_run_command_k8s(item, name, networks)

    mapping_dict = {
        'namespace':  _make_k8s_mapping('namespace',  networks),
        'selector':   _make_k8s_mapping('selector',   []),
        'depends_on': _make_k8s_mapping('depends_on', depends_on),
    }

    return BlueprintKubernetes(
        name              = name,
        unit_file_name    = f'_launcher_{name}.service',
        type_             = KUBERNETES_POD_TYPE,
        path              = '',
        command           = command,
        namespace         = ns,
        labels            = dict(item['metadata'].get('labels') or {}),
        container_name    = name,
        stop_command      = f'podman stop {name}',
        cleanup_command   = f'podman rm {name}',
        unit_type         = _KUBERNETES_POD_UNIT_TYPE,
        remain_after_exit = False,
        failure_behavior  = failure_behavior_default,
        mapping_behavior  = mapping_behavior_default,
        mapping_count     = len(mapping_dict),
        mapping_dict      = mapping_dict,
    )


def _build_k8s_service(
    item                     : dict,
    pods                     : list[dict],
    declared_namespaces      : set[str],
    failure_behavior_default : str,
    mapping_behavior_default : str,
) -> BlueprintKubernetes:
    """
    Build one Service Blueprint — a no-exec sync-point unit. Its 'selector'
    mapping is resolved here at build time: after = every Pod item in the
    same namespace whose labels satisfy spec.selector.
    """
    ns   = _k8s_instance_namespace(item)
    name = _k8s_instance_name(item)

    selector = item['spec']['selector']
    matched  = [
        _k8s_instance_name(pod) for pod in pods
        if _k8s_instance_namespace(pod) == ns and _match_k8s_selector(selector, pod)
    ]

    mapping_dict = {
        'namespace':  _make_k8s_mapping('namespace', [ns] if ns in declared_namespaces else []),
        'selector':   _make_k8s_mapping('selector',  matched),
        'depends_on': _make_k8s_mapping('depends_on', []),
    }

    return BlueprintKubernetes(
        name              = name,
        unit_file_name    = f'_launcher_{name}.service',
        type_             = KUBERNETES_SERVICE_TYPE,
        path              = '',
        command           = _KUBERNETES_SERVICE_COMMAND,
        namespace         = ns,
        labels            = dict(item['metadata'].get('labels') or {}),
        container_name    = '',
        stop_command      = '',
        cleanup_command   = '',
        unit_type         = _KUBERNETES_GROUP_UNIT_TYPE,
        remain_after_exit = True,
        failure_behavior  = failure_behavior_default,
        mapping_behavior  = mapping_behavior_default,
        mapping_count     = len(mapping_dict),
        mapping_dict      = mapping_dict,
    )


def _build_podman_run_command_k8s(item: dict, container_name: str, networks: list[str]) -> str:
    """
    Build the `podman run …` command string from spec.containers[0].

    Single-container scope: additional containers are skipped — expand
    when a real case needs them (same precedent as compose's long-form
    ports/volumes). K8s `command:` overrides the image ENTRYPOINT →
    `--entrypoint`; `args:` override the image CMD → trailing argv.
    `-p` is emitted only for ports carrying hostPort (containerPort alone
    publishes nothing on the host). `networks` carries the pod's full
    attachment list — identity namespace (absent when undeclared, rather
    than pointing at a network no unit creates) plus `launcher/networks`
    extras — one `--network` flag each, like compose multi-network
    services.
    """
    container = item['spec']['containers'][0]

    parts = ['podman run', '--sdnotify=conmon', f'--name {container_name}']

    for network in networks:
        parts.append(f'--network {network}')

    for port in container.get('ports') or []:
        if 'hostPort' in port:
            parts.append(f"-p {port['hostPort']}:{port['containerPort']}")

    for env in container.get('env') or []:
        parts.append(f"-e {env['name']}={env.get('value', '')}")

    command = container.get('command') or []
    if command:
        parts.append(f"--entrypoint {' '.join(command)}")

    working_dir = container.get('workingDir', '')
    if working_dir:
        parts.append(f'--workdir {working_dir}')

    parts.append(container['image'])

    args = container.get('args') or []
    if args:
        parts.append(' '.join(args))

    return ' '.join(parts)

#############################################################################
#############################################################################
# END KUBERNETES
#############################################################################
#############################################################################
