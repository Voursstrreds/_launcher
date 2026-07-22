import os


def write_manifest(
    blueprints    : list,
    manifest_path : str,
    mapping_names : list[str],
) -> None:
    """
    Writes the full manifest file for the run.

    Format:
      GLOBALS
      hierarchical_mappings=<space-separated mapping names>
      END

      INSTANCE
      name=<instance name>
      type=<discriminator>
      unit_file_name=<file>
      path=<exec path; empty for compose>
      command=<launch command>
      failure_behavior=<Abort|Restart>
      MAPPING <mapping_name>
      after=<space-separated peer names>
      before=<space-separated peer names>
      mapping_behavior=<Ignore|Cascade>
      END_MAPPING
      ... (one MAPPING block per mapping_name) ...
      END

      ... (one INSTANCE block per Blueprint) ...

    failure_behavior is instance-level (one per unit). mapping_behavior
    stays per-mapping.

    `after=` and `before=` are written as separate lines inside each MAPPING
    sub-block so both sides of the relation are preserved. The C-side reader
    consumes whichever side it needs for its scheduling logic.

    Parameters
    ----------
    blueprints    : list of Blueprint instances (in iteration order).
    manifest_path : absolute or relative path where the manifest is written;
                    parent directory is created if absent.
    mapping_names : ordered union of mapping names across all Blueprints
                    (computed once by the caller).
    """
    parent = os.path.dirname(manifest_path)
    if parent:
        os.makedirs(parent, exist_ok=True)

    with open(manifest_path, 'w') as f:
        _write_globals_block(f, mapping_names)
        for bp in blueprints:
            _write_instance_block(f, bp)


def _write_globals_block(f, mapping_names: list[str]) -> None:
    f.write('GLOBALS\n')
    f.write(f'hierarchical_mappings={" ".join(mapping_names)}\n')
    f.write('END\n\n')


def _write_instance_block(f, bp) -> None:
    f.write('INSTANCE\n')
    f.write(f'name={bp.name}\n')
    f.write(f'type={bp.type_}\n')
    f.write(f'unit_file_name={bp.unit_file_name}\n')
    f.write(f'path={bp.path}\n')
    f.write(f'command={bp.command}\n')
    f.write(f'failure_behavior={bp.failure_behavior}\n')

    for mapping_name, m in bp.mapping_dict.items():
        f.write(f'MAPPING {mapping_name}\n')
        f.write(f'after={" ".join(m.after)}\n')
        f.write(f'before={" ".join(m.before)}\n')
        f.write(f'mapping_behavior={bp.mapping_behavior}\n')
        f.write('END_MAPPING\n')

    f.write('END\n\n')
