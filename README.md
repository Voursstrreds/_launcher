# _launcher

A tool that turns a launch system into **systemd user services**, then starts and supervises them.

## How Does _launcher Work

Launcher has two halves:

1. **Unit generator (Python).** Reads an input YAML, validates it against a
   rule-set (podman-compose or kubernetes), and emits one systemd unit file
   per service/network plus a manifest describing their dependencies.

2. **Manager (C).** Copies the generated unit files into place, then talks to
   systemd over D-Bus to start each unit in dependency order and monitor its
   lifecycle until everything has settled (started, finished, or failed).

## Dependencies

**System**

- `gcc` — to compile the Manager
- `libsystemd` (development headers) — provides **sd-bus**, the systemd D-Bus
  API the Manager uses to start and track units (linked with `-lsystemd`)
- `systemd` with a running user instance — the units are user services
- `graphviz` (optional) — for the dependency-graph visualization

**Python** (installed into a local `venv` by `setup.sh`, see
`requirements.txt`)

- `Cerberus` — input schema validation
- `PyYAML` — YAML parsing
- `graphviz` — dependency-graph rendering

## Setup

Run the setup script from the project root:

```bash
./setup.sh
```

This creates a Python virtual environment, installs the Python dependencies,
compiles the Manager into `./bin/Manager`, and prepares the systemd user
directory.

## Configuration

The Manager reads its settings from `./etc/Launcher.config`. It is a simple
`KEY=VALUE` file — one entry per line, blank lines and lines starting with `#`
are ignored. All paths accept `~` and are resolved to absolute paths. Every
field below must be set (the eight paths are required; the four others have
defaults).

**Paths**

- `INPUT_FILE` — the input YAML to read (must already exist)
- `LAUNCHER_SCRIPT` — the Python entry point (`UnitGenerator.py`) to run
  (must already exist)
- `VENV_DIRECTORY` — the Python virtual environment to run the launcher in
  (must already exist)
- `RESULTS_FOLDER` — base directory for generated output
- `UNIT_FILE_STAGING_DIRECTORY` — where the generator writes the unit files
- `UNIT_FILE_DESTINATION` — where the Manager copies unit files so systemd
  picks them up (e.g. `~/.config/systemd/user`)
- `MANIFEST_FILE` — the manifest file; written in generate modes, read in
  start-only modes
- `LOG_DIRECTORY` — where the Manager writes its run logs

**Modes and failure-handling defaults**

These fields only accept a fixed set of values:

| Field | Possible values | Default | Meaning |
|-------|-----------------|---------|---------|
| `PROGRAM_MODE` | `generate` | — | Generate the manifest and unit files, then exit |
| | `generate-start` | | Generate, then start the units |
| | `generate-start-monitor` | **(default)** | Generate, start, then monitor until units settle |
| | `start` | | Reuse an existing manifest and start the units |
| | `start-monitor` | | Reuse an existing manifest, start, then monitor |
| `OPERATION_MODE` | `basic` | — | Exit once every unit is started or aborted |
| | `monitor` | | Exit once every unit has finished or aborted |
| `FAILURE_BEHAVIOR_DEFAULT` | `Abort` | `Abort` | On failure, give up on the unit |
| | `Restart` | | On failure, restart the unit |
| `MAPPING_BEHAVIOR_DEFAULT` | `Ignore` | `Ignore` | Contain a failure to the failed unit |
| | `Cascade` | | Propagate a failure to dependent units |

`OPERATION_MODE` is overridden by `PROGRAM_MODE` on conflict — the `*-monitor`
program modes force `monitor`, the rest force `basic`. The failure-handling
defaults apply only to units that don't specify their own.

### Setting `PROGRAM_MODE`

`PROGRAM_MODE` controls how far the pipeline runs. Set it in the config file
`./etc/Launcher.config` by adding or editing the line, for example:

```
PROGRAM_MODE=generate-start-monitor
```

Change the value to any of the five listed above and re-run `./bin/Manager` —
no recompilation needed. If the line is omitted (or the value is
unrecognized), the Manager falls back to `generate-start-monitor`.

### Selecting the input rule-set (`ACTIVE_RULE_SET`)

The input DSL is chosen by the `ACTIVE_RULE_SET` variable at the bottom of
`Rules.py`. It decides which schema validates the input and how it is turned
into unit files.

```python
ACTIVE_RULE_SET = PODMAN_COMPOSE
```

Possible values:

| Value | Input format |
|-------|--------------|
| `PODMAN_COMPOSE` | podman-compose YAML (default) |
| `KUBERNETES` | kubernetes YAML (`kind: List` of Namespace / Pod / Service) |

To switch, set `ACTIVE_RULE_SET` to the other value and re-run.

**Creating a new rule-set.** A `RuleSet` binds a schema to a builder; the two
emitters (`unit_file_builder`, `manifest_builder`) are generic and reused. To
add support for a new DSL:

1. Add the input schema (a Cerberus dict) in `Schemas.py`.
2. Add a `blueprint_builder` function in `Builders.py` that turns validated
   input into `Blueprint` objects (extend the `Blueprint` class if the DSL
   needs extra unit-file fields).
3. In `Rules.py`, import both and declare a new `RuleSet`, then point
   `ACTIVE_RULE_SET` at it:

   ```python
   MY_DSL = RuleSet(
       schema            = MY_DSL_SCHEMA,
       blueprint_builder = my_dsl_blueprint_builder,
       unit_file_builder = unit_file_builder,   # generic, reused
       manifest_builder  = manifest_builder,    # generic, reused
   )

   ACTIVE_RULE_SET = MY_DSL
   ```

## Running

Once configured, run the Manager:

```bash
./bin/Manager
```

Depending on `PROGRAM_MODE`, it will generate the unit files and manifest,
start the units, and (in monitor modes) wait until every unit has terminated
or failed.

## Results

After a run, the outputs are written to the locations set in your config:

| Output | Location | What it is |
|--------|----------|------------|
| Unit files | `UNIT_FILE_STAGING_DIRECTORY` | The generated `.service` files |
| Installed units | `UNIT_FILE_DESTINATION` | Same files copied where systemd loads them (e.g. `~/.config/systemd/user`) |
| Manifest | `MANIFEST_FILE` | The parsed instances and their dependencies |
| Logs | `LOG_DIRECTORY/manager.log` | The Manager's step-by-step run log |
| Dependency graph | `RESULTS_FOLDER/Topology/` | A Graphviz render of the unit dependency graph (skipped if `graphviz` is not installed) |

To inspect the results:

- **Read the run log** — `cat <LOG_DIRECTORY>/manager.log` shows what each step
  did and whether every unit settled or failed.
- **Check the running services** — since the units are systemd user services,
  use `systemctl --user status <unit>` or `systemctl --user list-units` to see
  their live state.
- **View the generated files** — the unit files and manifest are plain text in
  the paths above; open them directly.
