from __future__ import annotations
import os
from dataclasses import dataclass, field


@dataclass
class ManifestEntry:
    """
    Generic, rule-set-agnostic representation of one instance for the
    purpose of writing the manifest file.

    This dataclass defines the exact set of fields the manifest format
    carries. It knows nothing about GeneratedCommand or any other
    rule-set-specific type.

    Each rule-set wires its schemas + manifest_builder via the
    RuleSetDescriptor in Rules.py
    that maps its own command object to a ManifestEntry. The dump()
    method serialises one block in the agreed INI-block format.

    Fields:
        key                : top-level YAML identifier (e.g. 'Program1').
        name               : human-readable name (e.g. 'generic-task-1').
        unit_file_name     : systemd unit file name (e.g. 'generic-task-1.service').
        type_              : discriminator value as a string (e.g. 'ENTRY', 'GROUP').
        path               : absolute path to the executable; empty for groups.
        command            : full command string (path + arguments); empty for groups.
        after              : keys of instances this instance depends on.
        before             : keys of instances that depend on this instance.
        group              : keys of groups this instance belongs to.
        members            : keys of instances this group contains.
        order              : start-order index (-1 if not assigned).
        failure_behavior   : FailureBehavior in effect for this instance
                             (Abort / Restart — own-axis). Rule-sets that do
                             not model this leave the default 'Abort'.
        dependency_behavior: DependencyBehavior in effect for this instance
                             (Ignore / Cascade — dependency axis). Rule-sets
                             that do not model this leave the default 'Ignore'.
    """
    key                 : str
    name                : str
    unit_file_name      : str
    type_               : str
    path                : str       = ''
    command             : str       = ''
    after               : list[str] = field(default_factory=list)
    before              : list[str] = field(default_factory=list)
    group               : list[str] = field(default_factory=list)
    members             : list[str] = field(default_factory=list)
    order               : int       = -1
    failure_behavior    : str       = 'Abort'
    dependency_behavior : str       = 'Ignore'

    def dump(self, f) -> None:
        """
        Writes one INSTANCE...END block to the open file object f.
        List fields are written as space-separated values. Empty lists
        produce an empty value (e.g. 'members=') so the parser always
        sees every key.
        """
        f.write('INSTANCE\n')
        f.write(f'key={self.key}\n')
        f.write(f'name={self.name}\n')
        f.write(f'unit_file_name={self.unit_file_name}\n')
        f.write(f'type={self.type_}\n')
        f.write(f'path={self.path}\n')
        f.write(f'command={self.command}\n')
        f.write(f'after={" ".join(self.after)}\n')
        f.write(f'before={" ".join(self.before)}\n')
        f.write(f'group={" ".join(self.group)}\n')
        f.write(f'members={" ".join(self.members)}\n')
        f.write(f'order={self.order}\n')
        f.write(f'failure_behavior={self.failure_behavior}\n')
        f.write(f'dependency_behavior={self.dependency_behavior}\n')
        f.write('END\n')
        f.write('\n')


def write_manifest(instances: list, active_rule_set, manifest_path: str) -> None:
    """
    Writes the full manifest file for all instances.

    active_rule_set is received as a parameter rather than imported, breaking
    the circular dependency that would arise from importing Builders here
    while Builders imports ManifestEntry from this file.

    For each instance, calls active_rule_set.manifest_builder to produce a
    ManifestEntry, then calls its dump() method to write the block.
    write_manifest has no knowledge of GeneratedCommand or any
    rule-set-specific type.

    Parameters
    ----------
    instances     : list of command objects produced by CommandGenerator.
    active_rule_set : the RuleSetDescriptor instance selected in Rules.py.
    manifest_path : absolute path where the manifest file is written.
    """
    parent = os.path.dirname(manifest_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(manifest_path, 'w') as f:
        for instance in instances:
            entry = active_rule_set.manifest_builder(instance)
            entry.dump(f)
