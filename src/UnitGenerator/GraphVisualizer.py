"""
GraphVisualizer — produces Graphviz DOT renders of mapping graphs.

This module is a generic visualization tool. It receives a pre-computed
adjacency dict and draws it. It performs no validation.
"""
from __future__ import annotations
import os

import graphviz


def draw_mapping_graph(
    after_map   : dict[str, list[str]],
    output_dir  : str = './Results/Topology',
    graph_name  : str = 'mapping_graph',
    graph_label : str = 'Unified Mapping Graph',
) -> None:
    """
    Renders an after-adjacency map as a PNG image.

    Each edge X -> Y means "X starts after Y" (X depends on Y). Layout
    flows top-to-bottom so root nodes (no incoming edges) sit at the top.

    Silently warns and continues if the Graphviz `dot` executable is not
    installed at the OS level.

    Parameters
    ----------
    after_map : dict[str, list[str]]
        { X: [instances X starts after] }
    output_dir : str
        Directory for output files. Created if absent.
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

    for node in after_map:
        dot.node(node)

    for source, targets in after_map.items():
        for target in targets:
            dot.edge(source, target)

    try:
        dot.render(directory=output_dir, cleanup=False)
    except graphviz.ExecutableNotFound:
        print("  Warning: Graphviz 'dot' not found. "
              "Skipping mapping graph render.")
