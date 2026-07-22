def detect_cycle(adjacency: dict[str, list[str]], graph_name: str) -> None:
    """
    DFS-based cycle detection on a directed graph.

    Raises SystemExit with a descriptive message if a cycle is found.

    Parameters
    ----------
    adjacency : dict[str, list[str]]
        { node: [neighbour nodes] }
    graph_name : str
        Human-readable name included in the error message.
    """
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {key: WHITE for key in adjacency}
    path: list[str] = []

    def dfs(node):
        color[node] = GRAY
        path.append(node)
        for neighbor in adjacency.get(node, []):
            if neighbor not in color:
                continue
            if color[neighbor] == GRAY:
                cycle_start = path.index(neighbor)
                cycle = path[cycle_start:] + [neighbor]
                raise SystemExit(
                    f"{graph_name} cycle detected: "
                    f"{' -> '.join(cycle)}"
                )
            if color[neighbor] == WHITE:
                dfs(neighbor)
        path.pop()
        color[node] = BLACK

    for node in adjacency:
        if color[node] == WHITE:
            dfs(node)


def before_filler(
    all_instances : dict,
    after_field   : str,
    before_field  : str,
) -> dict[str, list[str]]:
    """
    Fills the `before` side of every instance.

    For each instance X, the result list is:
      - X's directly declared `before` (from `before_field`), plus
      - every peer Y where X appears in Y's `after_field` (Y.after contains
        X means X starts before Y).

    "Fills the before field by looking at the after field" — peers' afters
    are scanned and inverted onto this instance's befores. Direct
    declarations on the before side are preserved.

    Parameters
    ----------
    all_instances : dict
        { instance_key: { after_field: [...], before_field: [...] } }
    after_field, before_field : str
        Field names within each per-instance dict.

    Returns
    -------
    dict[str, list[str]]
        { instance_key: fully-filled before list }. Every key in
        all_instances appears, even if its list is empty.
    """
    result: dict[str, list[str]] = {
        key: list(fields.get(before_field, []))
        for key, fields in all_instances.items()
    }

    for key, fields in all_instances.items():
        for parent in fields.get(after_field, []):
            if parent in result and key not in result[parent]:
                result[parent].append(key)

    return result


def after_filler(
    all_instances : dict,
    after_field   : str,
    before_field  : str,
) -> dict[str, list[str]]:
    """
    Fills the `after` side of every instance. Symmetric mirror of
    `before_filler`.

    For each instance X, the result list is:
      - X's directly declared `after` (from `after_field`), plus
      - every peer Y where X appears in Y's `before_field` (Y.before
        contains X means Y starts before X, equivalently X starts after Y).

    "Fills the after field by looking at the before field" — peers' befores
    are scanned and inverted onto this instance's afters. Direct
    declarations on the after side are preserved.

    Returns
    -------
    dict[str, list[str]]
        { instance_key: fully-filled after list }.
    """
    result: dict[str, list[str]] = {
        key: list(fields.get(after_field, []))
        for key, fields in all_instances.items()
    }

    for key, fields in all_instances.items():
        for child in fields.get(before_field, []):
            if child in result and key not in result[child]:
                result[child].append(key)

    return result


def unify_mappings(
    map_a : dict[str, list[str]],
    map_b : dict[str, list[str]],
) -> dict[str, list[str]]:
    """
    Merges two per-key adjacency maps into one.

    The result's keys are the union of map_a and map_b keys; per-key value
    lists are the deduped concatenation. Used to combine per-mapping
    after-adjacencies into one unified graph before DAG validation.

    Parameters
    ----------
    map_a, map_b : dict[str, list[str]]
        { key: [neighbours] }. Either may be empty.

    Returns
    -------
    dict[str, list[str]]
        Union of the two adjacency maps, dedupe-preserving insertion order.
    """
    all_keys = dict.fromkeys(list(map_a.keys()) + list(map_b.keys()))
    unified: dict[str, list[str]] = {}
    for key in all_keys:
        unified[key] = list(dict.fromkeys(map_a.get(key, []) + map_b.get(key, [])))
    return unified


def validate_unified_mapping_dag(unified_after: dict[str, list[str]]) -> None:
    """
    Validates that the unified after-graph is a DAG.

    Edge X -> Y in `unified_after` means X must start after Y (X depends on
    Y across one or more mappings). A cycle here means the combined
    constraints are contradictory even if each individual mapping's graph
    is acyclic on its own.

    Raises SystemExit with the cycle path on failure.

    Parameters
    ----------
    unified_after : dict[str, list[str]]
        Merged after-adjacency map from sequential `unify_mappings` calls.
    """
    detect_cycle(unified_after, "Unified mapping")
