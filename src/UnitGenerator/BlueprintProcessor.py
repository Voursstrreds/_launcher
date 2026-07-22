from __future__ import annotations
import os
from abc         import ABC
from dataclasses import dataclass, field
from RelationshipMapping import (
    after_filler,
    before_filler,
    unify_mappings,
    validate_unified_mapping_dag,
)
from GraphVisualizer     import draw_mapping_graph
from UnitFileCreator     import Unit_File
from ManifestWriter      import write_manifest


#############################################################################
# BlueprintProcessor — the generic (rule-set-agnostic) Blueprint layer.
#
# Defines the Blueprint datastructure and every treatment that reads only
# the Blueprint superclass contract. No DSL knowledge lives here: whatever
# a rule-set adds on top of the superclass is declared in its extended
# Blueprint class and applied through the `apply_unit_file_settings` hook.
#
# Order of the files. The running order of the pipeline is declared by the
# RuleSet fields in ./Rules.py (field declaration order = running order);
# UnitGenerator.run() consumes those fields top to bottom:
#
#   step P1      Validator.py           validation  — generic engine driven
#                                       by the rule-set schema (./Schemas.py)
#   steps P2-P4  Builders.py            blueprint_builder — rule-set half:
#                                       constructs the extended Blueprints,
#                                       then calls the generic reconcile /
#                                       DAG-validate / draw helpers below
#   step P5      BlueprintProcessor.py  unit_file_builder — generic; writes
#                                       through UnitFileCreator.Unit_File
#   step P6      BlueprintProcessor.py  manifest_builder — generic; writes
#                                       through ManifestWriter.write_manifest
#############################################################################


#############################################################################
# Allowed-value constants.
#
# Behavior strings mirror what the C parser (Manager.c) reads verbatim from
# the manifest. They are rule-set-independent vocabulary: every DSL's
# Blueprints carry them, and the generic dumpers consume them.
#############################################################################

FAILURE_BEHAVIOR_ABORT   = 'Abort'
FAILURE_BEHAVIOR_RESTART = 'Restart'

MAPPING_BEHAVIOR_IGNORE  = 'Ignore'
MAPPING_BEHAVIOR_CASCADE = 'Cascade'

# ---------------------------------------------------------------------------
# Mapping side — which side of a Mapping (after / before) a DSL keyword
# imposes when it lists peers. Some keywords say "I start AFTER these";
# some hypothetically could say "I start BEFORE these". Reconciliation
# fills the inverse side either way, so the chosen side is purely about
# where the values land at Blueprint-build time. The keyword → side lookup
# itself is rule-set knowledge and lives with the rule-set (./Builders.py).
# ---------------------------------------------------------------------------

MAPPING_SIDE_AFTER  = 'after'
MAPPING_SIDE_BEFORE = 'before'

# ---------------------------------------------------------------------------
# Own-axis Restart=on-failure rate-limit knobs, written when
# `failure_behavior == FAILURE_BEHAVIOR_RESTART`. Generic policy behind the
# global FAILURE_BEHAVIOR choice — not tied to any DSL.
# ---------------------------------------------------------------------------

_RESTART_POLICY           = 'on-failure'
_RESTART_SEC              = '2'
_START_LIMIT_BURST        = '5'
_START_LIMIT_INTERVAL_SEC = '30'


#############################################################################
# Mapping — one hierarchical-mapping entry held inside a Blueprint.
#############################################################################

@dataclass
class Mapping:
    """
    One hierarchical-mapping entry inside a Blueprint.

    After step 3 of the pipeline (reconciliation) `after` and `before` are
    both populated bidirectionally regardless of which side the user
    declared in the YAML. Step 2 produces the raw form where only the
    declared side is filled.

    Fields:
        name          : mapping descriptor (e.g. 'depends_on', 'networks').
                        Redundant with the dict key in `mapping_dict` but
                        kept per spec so a Mapping can travel standalone.
        after         : names of instances this one starts after (parents).
        before        : names of instances this one starts before (children).
        after_length  : len(after); maintained in parallel with the list,
                        mirroring the C-side per-mapping length array.
        before_length : len(before); same.
    """
    name          : str
    after         : list[str] = field(default_factory=list)
    before        : list[str] = field(default_factory=list)
    after_length  : int       = 0
    before_length : int       = 0


#############################################################################
# Blueprint — abstract base for per-instance Blueprints.
#
# Carries the cross-DSL contract every concrete Blueprint must hold so the
# manifest dumper and the unit-file dumper can read it without DSL-specific
# knowledge. One concrete subclass per DSL (e.g. BlueprintPodmanCompose).
#############################################################################

@dataclass(kw_only=True)
class Blueprint(ABC):
    """
    Abstract base for per-instance Blueprints.

    Carries the nine fields the manifest dumper and the unit-file dumper
    consume regardless of which DSL produced the Blueprint. Subclasses
    extend with DSL-specific fields when those are needed, and override
    `apply_unit_file_settings` to say how those fields land in the unit
    file — both are completely left to the rule-set author.

    Fields:
        name             : the instance name.
        unit_file_name   : systemd unit filename (e.g. '<name>.service').
        type_            : DSL-specific discriminator string (e.g. 'SERVICE'
                           / 'NETWORK' for compose). Written verbatim into
                           the manifest's `type=` field.
        path             : executable path. Empty when `command` carries the
                           full invocation (the compose case).
        command          : full launch command.
        failure_behavior : 'Abort' or 'Restart' — own-axis policy.
        mapping_behavior : 'Ignore' or 'Cascade' — dependency-axis policy.
        mapping_count    : len(mapping_dict). Mirrors the C-side
                           `mapping_count` per-instance field verbatim.
        mapping_dict     : {mapping_name: Mapping}. Bidirectionally
                           reconciled after pipeline step 3.
    """
    name             : str
    unit_file_name   : str
    type_            : str
    path             : str                = ''
    command          : str                = ''
    failure_behavior : str                = FAILURE_BEHAVIOR_ABORT
    mapping_behavior : str                = MAPPING_BEHAVIOR_IGNORE
    mapping_count    : int                = 0
    mapping_dict     : dict[str, Mapping] = field(default_factory=dict)

    def apply_unit_file_settings(self, unit_file: Unit_File) -> None:
        """
        Extension hook: write the unit-file lines that come from the
        extended class's optional fields. Called by `_emit_one_unit_file`
        after every base-contract line is filled, right before the dump.

        No-op here — the superclass carries nothing the generic emitter
        does not already handle. Extended classes override it (e.g. the
        compose Blueprint writes Type= / NotifyAccess= / RemainAfterExit=
        / ExecStop= / ExecStopPost= from its own fields).
        """


#############################################################################
# Step 2 helper — Mapping construction.
#############################################################################

def make_mapping(name: str, values: list[str], side: str) -> Mapping:
    """
    Build a `Mapping` for one DSL keyword, placing the listed values on
    `side` (MAPPING_SIDE_AFTER or MAPPING_SIDE_BEFORE) — the side the
    keyword imposes, looked up by the rule-set before calling here. The
    opposite side stays empty and is filled by the reconciliation step
    (`reconcile_mappings`).
    """
    if side == MAPPING_SIDE_AFTER:
        return Mapping(
            name          = name,
            after         = list(values),
            before        = [],
            after_length  = len(values),
            before_length = 0,
        )
    return Mapping(
        name          = name,
        after         = [],
        before        = list(values),
        after_length  = 0,
        before_length = len(values),
    )


#############################################################################
# Steps 3 + 4 — reconciliation, DAG validation, visualization.
# Implemented via the rule-set-agnostic helpers in RelationshipMapping.py
# and GraphVisualizer.py.
#
# The helpers accept a per-instance "fields dict" view (`all_instances`).
# Blueprints carry mappings inside a per-mapping `Mapping` object, so the
# adapters below construct a synthetic view per mapping name and feed it
# through the generic helpers — one call per mapping.
#############################################################################

def _collect_mapping_names(blueprints: list[Blueprint]) -> list[str]:
    """Ordered union of mapping names across all Blueprints."""
    seen: list[str] = []
    for bp in blueprints:
        for name in bp.mapping_dict:
            if name not in seen:
                seen.append(name)
    return seen


def reconcile_mappings(blueprints: list[Blueprint]) -> None:
    """
    Step 3. For each mapping, fill both sides via the two fillers from
    `RelationshipMapping`:

      - `before_filler` fills the `before` side by reading peers' `after`.
      - `after_filler`  fills the `after`  side by reading peers' `before`.

    Both are needed: a keyword may impose either side (per the rule-set's
    keyword → side map), and the opposite side must be inferred from
    peers' declarations.

    Idempotent — re-running on already-reconciled Blueprints reproduces
    the same state because each filler dedupes its inputs.
    """
    by_name = {bp.name: bp for bp in blueprints}

    for mapping_name in _collect_mapping_names(blueprints):
        view = {
            bp.name: {
                MAPPING_SIDE_AFTER : (
                    bp.mapping_dict[mapping_name].after
                    if mapping_name in bp.mapping_dict else []
                ),
                MAPPING_SIDE_BEFORE: (
                    bp.mapping_dict[mapping_name].before
                    if mapping_name in bp.mapping_dict else []
                ),
            }
            for bp in blueprints
        }
        filled_before = before_filler(view, MAPPING_SIDE_AFTER, MAPPING_SIDE_BEFORE)
        filled_after  = after_filler (view, MAPPING_SIDE_AFTER, MAPPING_SIDE_BEFORE)

        for bp_name in filled_after:
            bp = by_name[bp_name]
            if mapping_name not in bp.mapping_dict:
                continue
            m               = bp.mapping_dict[mapping_name]
            m.after         = list(filled_after[bp_name])
            m.after_length  = len(m.after)
            m.before        = list(filled_before[bp_name])
            m.before_length = len(m.before)


def validate_blueprint_dag(blueprints: list[Blueprint]) -> dict[str, list[str]]:
    """
    Step 4. Build per-mapping after-adjacency, merge across all mappings
    via `unify_mappings`, then cycle-check via `validate_unified_mapping_dag`.
    Returns the merged adjacency so callers can feed it to the visualizer.
    Raises SystemExit on cycle, with the cycle path.
    """
    unified: dict[str, list[str]] = {bp.name: [] for bp in blueprints}

    for mapping_name in _collect_mapping_names(blueprints):
        after_adj = {
            bp.name: (
                bp.mapping_dict[mapping_name].after
                if mapping_name in bp.mapping_dict
                else []
            )
            for bp in blueprints
        }
        unified = unify_mappings(unified, after_adj)

    validate_unified_mapping_dag(unified)
    return unified


def draw_unified_graph(unified_after: dict[str, list[str]]) -> None:
    """
    Render the unified after-graph (edge X → Y means X starts after Y).
    Side-effect only; uses `draw_mapping_graph` which silently skips if
    the Graphviz `dot` executable is not installed.
    """
    draw_mapping_graph(
        unified_after,
        graph_name  = 'mapping_graph',
        graph_label = 'Unified Mapping Graph',
    )


#############################################################################
# Step 5 — unit-file emission. Reads only the Blueprint superclass contract;
# extended-class fields enter through the `apply_unit_file_settings` hook.
#############################################################################

def unit_file_builder(
    blueprints  : list[Blueprint],
    output_path : str,
) -> None:
    """
    Step 5: emit one systemd unit file per Blueprint to `output_path`.
    A trailing '/' is added to `output_path` if absent. Per-instance
    content is built via `_emit_one_unit_file`, which fills a `Unit_File`
    from the base contract and then lets the Blueprint's extended class
    apply its own settings through the hook.
    """
    if not output_path.endswith('/'):
        output_path += '/'
    os.makedirs(output_path, exist_ok=True)

    name_to_unit_file = {bp.name: bp.unit_file_name for bp in blueprints}

    for bp in blueprints:
        _emit_one_unit_file(bp, name_to_unit_file, output_path)


def _gather_peer_names(
    bp   : Blueprint,
    side : str,
) -> list[str]:
    """
    Union of `side` peer-names across all mappings on this Blueprint,
    deduped in insertion order. `side` is MAPPING_SIDE_AFTER or
    MAPPING_SIDE_BEFORE.
    """
    seen: list[str] = []
    for m in bp.mapping_dict.values():
        for peer in (m.after if side == MAPPING_SIDE_AFTER else m.before):
            if peer not in seen:
                seen.append(peer)
    return seen


def _resolve_unit_file_names(
    names             : list[str],
    name_to_unit_file : dict[str, str],
) -> str:
    """Space-join the unit-file names corresponding to known peer names."""
    return ' '.join(
        name_to_unit_file[name] for name in names if name in name_to_unit_file
    )


def _emit_one_unit_file(
    bp                : Blueprint,
    name_to_unit_file : dict[str, str],
    output_path       : str,
) -> None:
    """
    Fill a `Unit_File` from a single Blueprint and dump it.

    Everything written here derives from the superclass contract alone:
    the mapping-relation settings (Before/RequiredBy, After/Requires,
    PartOf on Cascade) from `mapping_dict` + `mapping_behavior`, the
    launch command (ExecStart) from `command`, and the global-choice
    Restart block from `failure_behavior`. Extended-class settings are
    applied by the `apply_unit_file_settings` hook just before the dump.
    """
    after_names  = _gather_peer_names(bp, MAPPING_SIDE_AFTER)
    before_names = _gather_peer_names(bp, MAPPING_SIDE_BEFORE)

    after_value  = _resolve_unit_file_names(after_names,  name_to_unit_file)
    before_value = _resolve_unit_file_names(before_names, name_to_unit_file)

    uf = Unit_File()

    uf.edit_field('UNIT', 'Description', bp.unit_file_name + ' UNIT FILE')

    if before_value:
        uf.edit_field('UNIT',    'Before',     before_value)
        uf.edit_field('INSTALL', 'RequiredBy', before_value)

    if after_value:
        uf.edit_field('UNIT', 'After',    after_value)
        uf.edit_field('UNIT', 'Requires', after_value)

    if bp.mapping_behavior == MAPPING_BEHAVIOR_CASCADE and after_value:
        uf.edit_field('UNIT', 'PartOf', after_value)

    uf.edit_field('SERVICE', 'ExecStart', bp.command)

    if bp.failure_behavior == FAILURE_BEHAVIOR_RESTART:
        uf.edit_field('SERVICE', 'Restart',               _RESTART_POLICY)
        uf.edit_field('SERVICE', 'RestartSec',            _RESTART_SEC)
        uf.edit_field('UNIT',    'StartLimitBurst',       _START_LIMIT_BURST)
        uf.edit_field('UNIT',    'StartLimitIntervalSec', _START_LIMIT_INTERVAL_SEC)

    bp.apply_unit_file_settings(uf)

    uf.dump_unit_file(bp.unit_file_name, output_path)


#############################################################################
# Step 6 — manifest emission. Already fully generic: write_manifest reads
# only the Blueprint superclass contract.
#############################################################################

def manifest_builder(
    blueprints    : list[Blueprint],
    manifest_path : str,
) -> None:
    """
    Step 6: write the full manifest file. Dispatches to
    `ManifestWriter.write_manifest` after collecting the mapping-name set.
    """
    write_manifest(blueprints, manifest_path, _collect_mapping_names(blueprints))
