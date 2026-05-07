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
    path  = []

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


def validate_dag(all_instances: dict, depends_field: str, group_field: str, members_field: str) -> None:
    """
    Validates that the dependency graph and the group membership graph
    are both directed acyclic graphs (DAGs).

    Performs a DFS-based cycle detection on two edge sets:

    1. Dependency edges: instance A lists B in its depends_field means
       A depends on B, i.e. edge A -> B.

    2. Group membership edges: instance A lists G in its group_field means
       A is inside G, i.e. edge A -> G. Group G lists M in its
       members_field means M is inside G, i.e. edge M -> G.

    Raises SystemExit with a descriptive message if a cycle is detected
    in either graph.

    Parameters
    ----------
    all_instances : dict
        The full normalised validated input dict,
        { instance_key: field_dict }.
    depends_field : str
        The name of the field that carries forward dependencies.
    group_field : str
        The field name that lists the groups an instance belongs to.
    members_field : str
        The field name that lists the members a group contains.
    """
    # --- Dependency graph ---
    dep_adj: dict[str, list[str]] = {key: [] for key in all_instances}
    for key, fields in all_instances.items():
        for dep in fields.get(depends_field, []):
            if dep in dep_adj:
                dep_adj[key].append(dep)

    detect_cycle(dep_adj, "Dependency")

    # --- Group membership graph ---
    grp_adj: dict[str, list[str]] = {key: [] for key in all_instances}
    for key, fields in all_instances.items():
        for group_key in fields.get(group_field, []):
            if group_key in grp_adj:
                grp_adj[key].append(group_key)
        for member_key in fields.get(members_field, []):
            if member_key in grp_adj:
                grp_adj[member_key].append(key)

    detect_cycle(grp_adj, "Group membership")


def compute_before(all_instances: dict, depends_field: str) -> dict[str, list[str]]:
    """
    Computes the inverted dependency map across all instances.

    For each instance X, collects every instance Y whose depends_field
    list contains X. The result is:
        { X: [Y1, Y2, ...] }
    meaning X is required by Y1, Y2, ...

    Parameters
    ----------
    all_instances : dict
        The full normalised validated input dict,
        { instance_key: field_dict }.
    depends_field : str
        The name of the field that carries forward dependencies
        (e.g. 'Depends'). Passed in by the caller so this function
        carries no rule-set knowledge.

    Returns
    -------
    dict[str, list[str]]
        { instance_key: [keys of instances that depend on it] }
        Every instance key present in all_instances appears as a key
        in the result, even if its before-list is empty.
    """
    before: dict[str, list[str]] = {key: [] for key in all_instances}

    for key, fields in all_instances.items():
        for dependency in fields.get(depends_field, []):
            if dependency in before:
                before[dependency].append(key)

    return before


def compute_group_maps(
    all_instances : dict,
    group_field   : str,
    members_field : str,
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """
    Computes two complete, mutually consistent group maps by reconciling
    both the group_field and members_field declarations across all instances.

    An instance may declare its group membership via the group_field, or a
    group may declare its members via the members_field, or both. This
    function unifies both directions so neither source is lost.

    Parameters
    ----------
    all_instances : dict
        The full normalised validated input dict,
        { instance_key: field_dict }.
    group_field : str
        The field name that lists the groups an instance belongs to
        (e.g. 'Group').
    members_field : str
        The field name that lists the members a group contains
        (e.g. 'Members').

    Returns
    -------
    group_of : dict[str, list[str]]
        { instance_key: [group_keys this instance belongs to] }
        Every instance key appears, even if its list is empty.
    members_of : dict[str, list[str]]
        { instance_key: [member_keys this instance contains] }
        Every instance key appears, even if its list is empty.
    """
    group_of   : dict[str, list[str]] = {key: [] for key in all_instances}
    members_of : dict[str, list[str]] = {key: [] for key in all_instances}

    for key, fields in all_instances.items():

        # Forward: instance declares which groups it belongs to.
        for group_key in fields.get(group_field, []):
            if group_key not in group_of[key]:
                group_of[key].append(group_key)
            if group_key in members_of and key not in members_of[group_key]:
                members_of[group_key].append(key)

        # Inverse: group declares which instances are its members.
        for member_key in fields.get(members_field, []):
            if member_key not in members_of[key]:
                members_of[key].append(member_key)
            if member_key in group_of and key not in group_of[member_key]:
                group_of[member_key].append(key)

    return group_of, members_of


def compute_unified_before(
    before_map: dict[str, list[str]],
    group_of:   dict[str, list[str]],
) -> dict[str, list[str]]:
    """
    Merges the dependency before_map and the group_of map into a single
    unified before-after structure.

    For each instance X:
        unified_before[X] = before_map[X] + group_of[X]   (deduplicated)

    This means X must start before every instance in unified_before[X]:
      - before_map[X]: instances that depend on X (from Depends).
      - group_of[X]:   groups X belongs to (X starts before its groups).

    Parameters
    ----------
    before_map : dict[str, list[str]]
        Inverted dependency map from compute_before().
    group_of : dict[str, list[str]]
        Group membership map from compute_group_maps().

    Returns
    -------
    dict[str, list[str]]
        { instance_key: [keys of all instances this key starts before] }
        Every key from both input dicts appears in the result.
    """
    all_keys = dict.fromkeys(list(before_map.keys()) + list(group_of.keys()))
    unified: dict[str, list[str]] = {}

    for key in all_keys:
        combined = list(dict.fromkeys(
            before_map.get(key, []) + group_of.get(key, [])
        ))
        unified[key] = combined

    return unified


def validate_unified_dag(unified_before: dict[str, list[str]]) -> None:
    """
    Validates that the unified before-after graph is a DAG.

    The unified graph merges dependency and group membership edges
    into a single ordering structure.  A cycle here means the
    combined constraints are contradictory even if each individual
    graph (deps, groups) is acyclic on its own.

    Raises SystemExit with a descriptive message if a cycle is found.

    Parameters
    ----------
    unified_before : dict[str, list[str]]
        Merged before map from compute_unified_before().
        Edge X -> Y means X must start before Y.
    """
    detect_cycle(unified_before, "Unified (dependency + group)")
