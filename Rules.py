from __future__ import annotations
from dataclasses import dataclass
from typing      import Any, Callable
from Schemas            import PODMAN_COMPOSE_SCHEMA, KUBERNETES_SCHEMA
from Builders           import podman_compose_blueprint_builder, kubernetes_blueprint_builder
from BlueprintProcessor import unit_file_builder, manifest_builder


@dataclass
class RuleSet:
    """
    Active rule-set for one DSL. Points the unit-generation pipeline at the
    syntactic schema and the semantic operations.

    The field declaration order below IS the pipeline running order —
    UnitGenerator.run() consumes the fields top to bottom:

        schema            : step P1 — Cerberus schema dict consumed by the
                            validator stage. Rule-set half (./Schemas.py).
        blueprint_builder : steps P2-P4 — validated input → list of
                            Blueprints (raw construction + reconciliation +
                            DAG validation). Rule-set half (./Builders.py).
        unit_file_builder : step P5 — Blueprints → one systemd unit file
                            each. Generic (BlueprintProcessor.py); the
                            extended Blueprint class contributes its own
                            lines via the `apply_unit_file_settings` hook.
        manifest_builder  : step P6 — Blueprints → manifest file. Generic
                            (BlueprintProcessor.py).

    Only `schema` and `blueprint_builder` are rule-set-specific. The two
    emitters are generic and shared by every RuleSet; they are still bound
    here so the whole running order is read from one place.
    """
    schema            : dict
    blueprint_builder : Callable[..., Any]
    unit_file_builder : Callable[..., Any]
    manifest_builder  : Callable[..., Any]


# ---------------------------------------------------------------------------
# Rule-set instances and active selection. Note that both rule-sets bind
# the SAME generic emitters (unit_file_builder / manifest_builder from
# BlueprintProcessor) — only schema and blueprint_builder differ per DSL.
# ---------------------------------------------------------------------------

PODMAN_COMPOSE  = RuleSet(
    schema            = PODMAN_COMPOSE_SCHEMA,
    blueprint_builder = podman_compose_blueprint_builder,
    unit_file_builder = unit_file_builder,
    manifest_builder  = manifest_builder,
)

KUBERNETES      = RuleSet(
    schema            = KUBERNETES_SCHEMA,
    blueprint_builder = kubernetes_blueprint_builder,
    unit_file_builder = unit_file_builder,
    manifest_builder  = manifest_builder,
)

ACTIVE_RULE_SET = PODMAN_COMPOSE
