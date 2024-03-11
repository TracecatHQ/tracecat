def find_entrypoint(graph: dict[str, list[str]]) -> str:
    """Find the entrypoint of a workflow.

    This is the first node in the graph with no incoming edges.
    """

    nodes = find_entrypoints(graph)

    if len(nodes) == 0:
        raise ValueError("No entrypoints found.")
    return nodes.pop()


def find_entrypoints(graph: dict[str, list[str]]) -> list[str]:
    """Find the entrypoints of a graph.

    This is the first node in the graph with no incoming edges.
    """

    nodes = set(graph.keys())
    for edges in graph.values():
        nodes.difference_update(edges)

    return list(nodes)
