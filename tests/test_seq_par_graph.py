import json

from loguru import logger

from tracecat.experimental.dsl.seq_par_graph import RFGraph, parse_dag_to_blocks


# Traverse the DSL and count the number of nodes
def count_nodes(node):
    if "sequence" in node:
        return sum(count_nodes(n) for n in node["sequence"])
    if "parallel" in node:
        return sum(count_nodes(n) for n in node["parallel"])
    return 1


def test_parse_dag_simple_sequence():
    """Simple sequence

    A -> B -> C
    """
    rf_obj = {
        "nodes": [
            {
                "id": "a",
                "type": "action",
                "data": {"type": "action", "title": "action_a"},
            },
            {
                "id": "b",
                "type": "action",
                "data": {"type": "action", "title": "action_b"},
            },
            {
                "id": "c",
                "type": "action",
                "data": {"type": "action", "title": "action_c"},
            },
        ],
        "edges": [
            {"id": "a_b", "source": "a", "target": "b"},
            {"id": "b_c", "source": "b", "target": "c"},
        ],
    }

    expected_wf_ir = {
        "sequence": [
            {"ref": "a", "action": "action", "args": {}},
            {"ref": "b", "action": "action", "args": {}},
            {"ref": "c", "action": "action", "args": {}},
        ]
    }
    graph = RFGraph(rf_obj)
    dsl = parse_dag_to_blocks(graph)
    actual_wf_ir = dsl.root.model_dump()
    assert actual_wf_ir == expected_wf_ir
    assert count_nodes(actual_wf_ir) == 3


def test_kite():
    """Kite shape:

       A
       /\
      B  D
      |  |
      C  E
       \/
        F
        |
        G

    This can be expressed as
    root = seq(A, par(seq(B, C), seq(D, E)), F, G)
    """  # noqa: W605
    rf_obj = {
        "nodes": [
            {
                "id": "a",
                "type": "action",
                "data": {"type": "action", "title": "action_a"},
            },
            {
                "id": "b",
                "type": "action",
                "data": {"type": "action", "title": "action_b"},
            },
            {
                "id": "c",
                "type": "action",
                "data": {"type": "action", "title": "action_c"},
            },
            {
                "id": "d",
                "type": "action",
                "data": {"type": "action", "title": "action_d"},
            },
            {
                "id": "e",
                "type": "action",
                "data": {"type": "action", "title": "action_e"},
            },
            {
                "id": "f",
                "type": "action",
                "data": {"type": "action", "title": "action_f"},
            },
            {
                "id": "g",
                "type": "action",
                "data": {"type": "action", "title": "action_g"},
            },
        ],
        "edges": [
            {"id": "a_b", "source": "a", "target": "b"},
            {"id": "b_c", "source": "b", "target": "c"},
            {"id": "c_f", "source": "c", "target": "f"},
            {"id": "a_d", "source": "a", "target": "d"},
            {"id": "d_e", "source": "d", "target": "e"},
            {"id": "e_f", "source": "e", "target": "f"},
            {"id": "f_g", "source": "f", "target": "g"},
        ],
    }

    expected_wf_ir = {
        "sequence": [
            {"ref": "a", "action": "action", "args": {}},
            {
                "parallel": [
                    {
                        "sequence": [
                            {"ref": "b", "action": "action", "args": {}},
                            {"ref": "c", "action": "action", "args": {}},
                        ]
                    },
                    {
                        "sequence": [
                            {"ref": "d", "action": "action", "args": {}},
                            {"ref": "e", "action": "action", "args": {}},
                        ]
                    },
                ]
            },
            {"ref": "f", "action": "action", "args": {}},
            {"ref": "g", "action": "action", "args": {}},
        ]
    }
    dsl = parse_dag_to_blocks(RFGraph(rf_obj))
    actual_wf_ir = dsl.root.model_dump()
    assert actual_wf_ir == expected_wf_ir
    assert count_nodes(actual_wf_ir) == 7


def test_double_kite():
    """Double kite shape:

       A
       /\
      B  D
      |  |
      C  E
       \/
        F
        |
        G
       / \
      H   I
      |   |
      |   J     # Note H-K and i-J-K different lengths
      |   |
      K   L
       \ /
        M
    This can be expressed as
    root = seq(
        A,
        par(seq(B, C), seq(D, E)),
        F,
        G,
        par(seq(H, K), seq(I, J, L)),
        M
    )
    """  # noqa: W605
    rf_obj = {
        "nodes": [
            {
                "id": "a",
                "type": "action",
                "data": {"type": "action", "title": "action_a"},
            },
            {
                "id": "b",
                "type": "action",
                "data": {"type": "action", "title": "action_b"},
            },
            {
                "id": "c",
                "type": "action",
                "data": {"type": "action", "title": "action_c"},
            },
            {
                "id": "d",
                "type": "action",
                "data": {"type": "action", "title": "action_d"},
            },
            {
                "id": "e",
                "type": "action",
                "data": {"type": "action", "title": "action_e"},
            },
            {
                "id": "f",
                "type": "action",
                "data": {"type": "action", "title": "action_f"},
            },
            {
                "id": "g",
                "type": "action",
                "data": {"type": "action", "title": "action_g"},
            },
            {
                "id": "h",
                "type": "action",
                "data": {"type": "action", "title": "action_h"},
            },
            {
                "id": "i",
                "type": "action",
                "data": {"type": "action", "title": "action_i"},
            },
            {
                "id": "j",
                "type": "action",
                "data": {"type": "action", "title": "action_j"},
            },
            {
                "id": "k",
                "type": "action",
                "data": {"type": "action", "title": "action_k"},
            },
            {
                "id": "l",
                "type": "action",
                "data": {"type": "action", "title": "action_l"},
            },
            {
                "id": "m",
                "type": "action",
                "data": {"type": "action", "title": "action_m"},
            },
        ],
        "edges": [
            {"id": "a_b", "source": "a", "target": "b"},
            {"id": "b_c", "source": "b", "target": "c"},
            {"id": "c_f", "source": "c", "target": "f"},
            {"id": "a_d", "source": "a", "target": "d"},
            {"id": "d_e", "source": "d", "target": "e"},
            {"id": "e_f", "source": "e", "target": "f"},
            {"id": "f_g", "source": "f", "target": "g"},
            {"id": "g_h", "source": "g", "target": "h"},
            {"id": "h_k", "source": "h", "target": "k"},
            {"id": "g_i", "source": "g", "target": "i"},
            {"id": "i_j", "source": "i", "target": "j"},
            {"id": "j_l", "source": "j", "target": "l"},
            {"id": "k_m", "source": "k", "target": "m"},
            {"id": "l_m", "source": "l", "target": "m"},
        ],
    }

    expected_wf_ir = {
        "sequence": [
            {"ref": "a", "action": "action", "args": {}},
            {
                "parallel": [
                    {
                        "sequence": [
                            {"ref": "b", "action": "action", "args": {}},
                            {"ref": "c", "action": "action", "args": {}},
                        ]
                    },
                    {
                        "sequence": [
                            {"ref": "d", "action": "action", "args": {}},
                            {"ref": "e", "action": "action", "args": {}},
                        ]
                    },
                ]
            },
            {"ref": "f", "action": "action", "args": {}},
            {"ref": "g", "action": "action", "args": {}},
            {
                "parallel": [
                    {
                        "sequence": [
                            {"ref": "h", "action": "action", "args": {}},
                            {"ref": "k", "action": "action", "args": {}},
                        ]
                    },
                    {
                        "sequence": [
                            {"ref": "i", "action": "action", "args": {}},
                            {"ref": "j", "action": "action", "args": {}},
                            {"ref": "l", "action": "action", "args": {}},
                        ]
                    },
                ]
            },
            {"ref": "m", "action": "action", "args": {}},
        ]
    }
    dsl = parse_dag_to_blocks(RFGraph(rf_obj))
    actual_wf_ir = dsl.root.model_dump()
    assert actual_wf_ir == expected_wf_ir
    assert count_nodes(actual_wf_ir) == 13


def test_tree_1():
    """Tree 1 shape:

       A
       /\
      B  c
     /|  |\
    D E  F G


    This can be expressed as
    root = seq(
        A,
        par(
            seq(B, par(D, E)),
            seq(C, par(F, G))
        )
    )
    """  # noqa: W605
    rf_obj = {
        "nodes": [
            {
                "id": "a",
                "type": "action",
                "data": {"type": "action", "title": "action_a"},
            },
            {
                "id": "b",
                "type": "action",
                "data": {"type": "action", "title": "action_b"},
            },
            {
                "id": "c",
                "type": "action",
                "data": {"type": "action", "title": "action_c"},
            },
            {
                "id": "d",
                "type": "action",
                "data": {"type": "action", "title": "action_d"},
            },
            {
                "id": "e",
                "type": "action",
                "data": {"type": "action", "title": "action_e"},
            },
            {
                "id": "f",
                "type": "action",
                "data": {"type": "action", "title": "action_f"},
            },
            {
                "id": "g",
                "type": "action",
                "data": {"type": "action", "title": "action_g"},
            },
        ],
        "edges": [
            {"id": "a_b", "source": "a", "target": "b"},
            {"id": "a_c", "source": "a", "target": "c"},
            {"id": "b_d", "source": "b", "target": "d"},
            {"id": "b_e", "source": "b", "target": "e"},
            {"id": "c_f", "source": "c", "target": "f"},
            {"id": "c_g", "source": "c", "target": "g"},
        ],
    }

    expected_wf_ir = {
        "sequence": [
            {"ref": "a", "action": "action", "args": {}},
            {
                "parallel": [
                    {
                        "sequence": [
                            {"ref": "b", "action": "action", "args": {}},
                            {
                                "parallel": [
                                    {"ref": "d", "action": "action", "args": {}},
                                    {"ref": "e", "action": "action", "args": {}},
                                ]
                            },
                        ]
                    },
                    {
                        "sequence": [
                            {"ref": "c", "action": "action", "args": {}},
                            {
                                "parallel": [
                                    {"ref": "f", "action": "action", "args": {}},
                                    {"ref": "g", "action": "action", "args": {}},
                                ]
                            },
                        ]
                    },
                ]
            },
        ]
    }
    dsl = parse_dag_to_blocks(RFGraph(rf_obj))
    actual_wf_ir = dsl.root.model_dump()
    assert actual_wf_ir == expected_wf_ir
    assert count_nodes(actual_wf_ir) == 7


def test_tree_2():
    """Tree 2 shape:

         A
        / \
       B   E
      /|   |
     C D   F
           |
           G
    This can be expressed as
    root = seq(
        A,
        par(
            seq(B, par(C, D)),
            seq(E, F, G)
        )
    )
    """  # noqa: W605
    rf_obj = {
        "nodes": [
            {
                "id": "a",
                "type": "action",
                "data": {"type": "action", "title": "action_a"},
            },
            {
                "id": "b",
                "type": "action",
                "data": {"type": "action", "title": "action_b"},
            },
            {
                "id": "c",
                "type": "action",
                "data": {"type": "action", "title": "action_c"},
            },
            {
                "id": "d",
                "type": "action",
                "data": {"type": "action", "title": "action_d"},
            },
            {
                "id": "e",
                "type": "action",
                "data": {"type": "action", "title": "action_e"},
            },
            {
                "id": "f",
                "type": "action",
                "data": {"type": "action", "title": "action_f"},
            },
            {
                "id": "g",
                "type": "action",
                "data": {"type": "action", "title": "action_g"},
            },
        ],
        "edges": [
            {"id": "a_b", "source": "a", "target": "b"},
            {"id": "a_e", "source": "a", "target": "e"},
            {"id": "b_c", "source": "b", "target": "c"},
            {"id": "b_d", "source": "b", "target": "d"},
            {"id": "e_f", "source": "e", "target": "f"},
            {"id": "f_g", "source": "f", "target": "g"},
        ],
    }

    expected_wf_ir = {
        "sequence": [
            {"ref": "a", "action": "action", "args": {}},
            {
                "parallel": [
                    {
                        "sequence": [
                            {"ref": "b", "action": "action", "args": {}},
                            {
                                "parallel": [
                                    {"ref": "c", "action": "action", "args": {}},
                                    {"ref": "d", "action": "action", "args": {}},
                                ]
                            },
                        ]
                    },
                    {
                        "sequence": [
                            {"ref": "e", "action": "action", "args": {}},
                            {"ref": "f", "action": "action", "args": {}},
                            {"ref": "g", "action": "action", "args": {}},
                        ]
                    },
                ]
            },
        ]
    }
    graph = RFGraph(rf_obj)
    graph.print_topsort_order()
    dsl = parse_dag_to_blocks(graph)
    actual_wf_ir = dsl.root.model_dump()
    assert actual_wf_ir == expected_wf_ir
    assert count_nodes(actual_wf_ir) == 7


def test_complex_dag_1():
    """Complex DAG shape:

         A
        / \
       B   C
      / \ / \
     D   E   F
      \  |  /
       \ | /
         G
    This can be expressed as
    root = seq(
        A,
        par(
            seq(B, D),
            seq(C, par(E, F))
        ),
        G
    )
    """  # noqa: W605
    rf_obj = {
        "nodes": [
            {
                "id": "a",
                "type": "action",
                "data": {"type": "action", "title": "action_a"},
            },
            {
                "id": "b",
                "type": "action",
                "data": {"type": "action", "title": "action_b"},
            },
            {
                "id": "c",
                "type": "action",
                "data": {"type": "action", "title": "action_c"},
            },
            {
                "id": "d",
                "type": "action",
                "data": {"type": "action", "title": "action_d"},
            },
            {
                "id": "e",
                "type": "action",
                "data": {"type": "action", "title": "action_e"},
            },
            {
                "id": "f",
                "type": "action",
                "data": {"type": "action", "title": "action_f"},
            },
            {
                "id": "g",
                "type": "action",
                "data": {"type": "action", "title": "action_g"},
            },
            {
                "id": "h",
                "type": "action",
                "data": {"type": "action", "title": "action_h"},
            },
            {
                "id": "i",
                "type": "action",
                "data": {"type": "action", "title": "action_i"},
            },
        ],
        "edges": [
            {"id": "a_b", "source": "a", "target": "b"},
            {"id": "a_c", "source": "a", "target": "c"},
            {"id": "b_d", "source": "b", "target": "d"},
            {"id": "b_e", "source": "b", "target": "e"},
            {"id": "c_e", "source": "c", "target": "e"},
            {"id": "c_f", "source": "c", "target": "f"},
            {"id": "d_g", "source": "d", "target": "g"},
            {"id": "e_g", "source": "e", "target": "g"},
            {"id": "f_g", "source": "f", "target": "g"},
        ],
    }

    expected_wf_ir = {
        "sequence": [
            {"ref": "a", "action": "action", "args": {}},
            {
                "parallel": [
                    {
                        "sequence": [
                            {"ref": "b", "action": "action", "args": {}},
                            {"ref": "d", "action": "action", "args": {}},
                        ]
                    },
                    {
                        "sequence": [
                            {"ref": "c", "action": "action", "args": {}},
                            {
                                "parallel": [
                                    {"ref": "e", "action": "action", "args": {}},
                                    {"ref": "f", "action": "action", "args": {}},
                                ]
                            },
                        ]
                    },
                ]
            },
            {"ref": "g", "action": "action", "args": {}},
        ]
    }
    graph = RFGraph(rf_obj)
    dsl = parse_dag_to_blocks(graph)
    actual_wf_ir = dsl.root.model_dump()

    logger.warning(json.dumps(graph.indegree, indent=2))
    logger.warning(json.dumps(actual_wf_ir, indent=2))
    assert actual_wf_ir == expected_wf_ir
    assert count_nodes(actual_wf_ir) == 11


def test_complex_dag_2():
    """Complex DAG shape:

         A
        / \
       B   C
      / \ / \
     D   E   F
      \ / \ /
       G   H
        \ /
         I
    This can be expressed as
    root = seq(
        A,
        par(
            seq(B, par(D, E), G),
            seq(C, par(E, F), H)
        ),
        I
    )
    """  # noqa: W605
    rf_obj = {
        "nodes": [
            {
                "id": "a",
                "type": "action",
                "data": {"type": "action", "title": "action_a"},
            },
            {
                "id": "b",
                "type": "action",
                "data": {"type": "action", "title": "action_b"},
            },
            {
                "id": "c",
                "type": "action",
                "data": {"type": "action", "title": "action_c"},
            },
            {
                "id": "d",
                "type": "action",
                "data": {"type": "action", "title": "action_d"},
            },
            {
                "id": "e",
                "type": "action",
                "data": {"type": "action", "title": "action_e"},
            },
            {
                "id": "f",
                "type": "action",
                "data": {"type": "action", "title": "action_f"},
            },
            {
                "id": "g",
                "type": "action",
                "data": {"type": "action", "title": "action_g"},
            },
            {
                "id": "h",
                "type": "action",
                "data": {"type": "action", "title": "action_h"},
            },
            {
                "id": "i",
                "type": "action",
                "data": {"type": "action", "title": "action_i"},
            },
        ],
        "edges": [
            {"id": "a_b", "source": "a", "target": "b"},
            {"id": "a_c", "source": "a", "target": "c"},
            {"id": "b_d", "source": "b", "target": "d"},
            {"id": "b_e", "source": "b", "target": "e"},
            {"id": "c_e", "source": "c", "target": "e"},
            {"id": "c_f", "source": "c", "target": "f"},
            {"id": "d_g", "source": "d", "target": "g"},
            {"id": "e_g", "source": "e", "target": "g"},
            {"id": "e_h", "source": "e", "target": "h"},
            {"id": "f_h", "source": "f", "target": "h"},
            {"id": "g_i", "source": "g", "target": "i"},
            {"id": "h_i", "source": "h", "target": "i"},
        ],
    }

    expected_wf_ir = {
        "sequence": [
            {"ref": "a", "action": "action", "args": {}},
            {
                "parallel": [
                    {
                        "sequence": [
                            {"ref": "b", "action": "action", "args": {}},
                            {
                                "parallel": [
                                    {"ref": "d", "action": "action", "args": {}},
                                    {"ref": "e", "action": "action", "args": {}},
                                ]
                            },
                            {"ref": "g", "action": "action", "args": {}},
                        ]
                    },
                    {
                        "sequence": [
                            {"ref": "c", "action": "action", "args": {}},
                            {
                                "parallel": [
                                    {"ref": "e", "action": "action", "args": {}},
                                    {"ref": "f", "action": "action", "args": {}},
                                ]
                            },
                            {"ref": "h", "action": "action", "args": {}},
                        ]
                    },
                ]
            },
            {"ref": "i", "action": "action", "args": {}},
        ]
    }
    graph = RFGraph(rf_obj)
    dsl = parse_dag_to_blocks(graph)
    actual_wf_ir = dsl.root.model_dump()

    logger.warning(json.dumps(graph.indegree, indent=2))
    logger.warning(json.dumps(actual_wf_ir, indent=2))
    assert actual_wf_ir == expected_wf_ir
    assert count_nodes(actual_wf_ir) == 11
