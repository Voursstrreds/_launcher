"""
GraphVisualizer — produces Graphviz DOT renders of dependency and
group-membership graphs.

This module is a generic visualization tool.  It receives pre-computed
adjacency dicts and draws them.  It performs no validation.
"""
from __future__ import annotations
import os

import graphviz


def draw_dependency_graph(
    before_map:  dict[str, list[str]],
    output_dir:  str = './Results/Topology',
    graph_name:  str = 'dependency_graph',
    graph_label: str = 'Dependency Graph',
) -> None:
    """
    Renders a before-map as a PNG image.

    Each edge X -> Y means "X starts before Y".  Layout flows
    top-to-bottom so root nodes (no incoming edges) sit at the top.

    Parameters
    ----------
    before_map : dict[str, list[str]]
        { X: [instances X starts before] }
    output_dir : str
        Directory for output files.  Created if absent.
    graph_name : str
        Graphviz graph name — also used as the output file stem.
    graph_label : str
        Title rendered at the top of the image.
    """
    os.makedirs(output_dir, exist_ok=True)

    dot = graphviz.Digraph(graph_name, format='png')

    dot.attr(
        rankdir='TB',
        bgcolor='white',
        dpi='150',
        label=graph_label,
        labelloc='t',
        fontsize='14',
        fontname='Helvetica',
    )
    dot.attr('node',
        shape='box',
        style='filled',
        fillcolor='#D6EAF8',
        fontname='Helvetica',
        fontsize='10',
    )
    dot.attr('edge',
        color='#2C3E50',
        arrowsize='0.7',
    )

    for node in before_map:
        dot.node(node)

    for source, targets in before_map.items():
        for target in targets:
            dot.edge(source, target)

    try:
        dot.render(directory=output_dir, cleanup=False)
    except graphviz.ExecutableNotFound:
        print("  Warning: Graphviz 'dot' not found. "
              "Skipping dependency graph render.")


def draw_group_graph(
    group_of:   dict[str, list[str]],
    members_of: dict[str, list[str]],
    output_dir: str = './Results/Topology',
) -> None:
    """
    Renders the group membership graph as a PNG image.

    Each edge M -> G means "M is a member of G".  Groups are identified
    as nodes whose members_of list is non-empty and are styled
    differently from regular nodes.

    Parameters
    ----------
    group_of : dict[str, list[str]]
        { instance_key: [groups it belongs to] }
    members_of : dict[str, list[str]]
        { instance_key: [members it contains] }
    output_dir : str
        Directory for output files.  Created if absent.
    """
    os.makedirs(output_dir, exist_ok=True)

    dot = graphviz.Digraph('group_graph', format='png')

    dot.attr(
        rankdir='TB',
        bgcolor='white',
        dpi='150',
        label='Group Membership Graph',
        labelloc='t',
        fontsize='14',
        fontname='Helvetica',
    )
    dot.attr('edge',
        color='#2C3E50',
        arrowsize='0.7',
    )

    group_keys = {key for key, members in members_of.items() if members}

    for node in group_of:
        if node in group_keys:
            dot.node(node,
                shape='ellipse',
                style='filled',
                fillcolor='#D5F5E3',
                fontname='Helvetica',
                fontsize='10',
            )
        else:
            dot.node(node,
                shape='box',
                style='filled',
                fillcolor='#D6EAF8',
                fontname='Helvetica',
                fontsize='10',
            )

    for member, groups in group_of.items():
        for group in groups:
            dot.edge(member, group)

    try:
        dot.render(directory=output_dir, cleanup=False)
    except graphviz.ExecutableNotFound:
        print("  Warning: Graphviz 'dot' not found. "
              "Skipping group graph render.")
