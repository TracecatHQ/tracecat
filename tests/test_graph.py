from tracecat.experimental.dsl.graph import RFGraph, react_flow_graph_to_dsl
from tracecat.experimental.dsl.workflow import ActionStatement


def assert_execution_ordering(graph: RFGraph, order: list[str]):
    """Assert that the ordering of the graph edges is respected."""
    node2pos = {node: i for i, node in enumerate(order)}
    assert all(node2pos[edge.source] < node2pos[edge.target] for edge in graph.edges)


def test_parse_dag_simple_sequence():
    """Simple sequence

    A -> B -> C

    Checks:
    1. The sequence is correctly parsed
    2. The depends_on field is correctly set
    3. Action refs (slugs) are correfly constructed
    """
    metadata = {"title": "Simple sequence", "description": "A -> B -> C"}
    rf_obj = {
        "nodes": [
            {
                "id": "a",
                "type": "core.action",
                "data": {
                    "type": "core.action",
                    "title": "Action A",
                    "args": {"test": 1},
                },
            },
            {
                "id": "b",
                "type": "core.action",
                "data": {
                    "type": "core.action",
                    "title": "Action B",
                    "args": {"test": 2},
                },
            },
            {
                "id": "c",
                "type": "core.action",
                "data": {
                    "type": "core.action",
                    "title": "Action C",
                    "args": {"test": 3},
                },
            },
        ],
        "edges": [
            {"id": "a_b", "source": "a", "target": "b"},
            {"id": "b_c", "source": "b", "target": "c"},
        ],
    }

    expected_wf_ir = [
        ActionStatement(
            ref="action_a",
            action="core.action",
            args={"test": 1},
        ),
        ActionStatement(
            ref="action_b",
            action="core.action",
            args={"test": 2},
            depends_on=["action_a"],
        ),
        ActionStatement(
            ref="action_c",
            action="core.action",
            args={"test": 3},
            depends_on=["action_b"],
        ),
    ]
    graph = RFGraph(rf_obj)
    dsl = react_flow_graph_to_dsl(graph, **metadata)
    assert dsl.actions == expected_wf_ir


def test_kite():
    r"""Kite shape:

       A
       /\
      B  D
      |  |
      C  E
       \/
        F
        |
        G

    """
    metadata = {"title": "Kite", "description": "Test kite shape"}
    rf_obj = {
        "nodes": [
            {
                "id": "a",
                "type": "core.action",
                "data": {"type": "core.action", "title": "action_a"},
            },
            {
                "id": "b",
                "type": "core.action",
                "data": {"type": "core.action", "title": "action_b"},
            },
            {
                "id": "c",
                "type": "core.action",
                "data": {"type": "core.action", "title": "action_c"},
            },
            {
                "id": "d",
                "type": "core.action",
                "data": {"type": "core.action", "title": "action_d"},
            },
            {
                "id": "e",
                "type": "core.action",
                "data": {"type": "core.action", "title": "action_e"},
            },
            {
                "id": "f",
                "type": "core.action",
                "data": {"type": "core.action", "title": "action_f"},
            },
            {
                "id": "g",
                "type": "core.action",
                "data": {"type": "core.action", "title": "action_g"},
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

    expected_wf_ir = [
        ActionStatement(ref="action_a", action="core.action", args={}),
        ActionStatement(
            ref="action_b", action="core.action", args={}, depends_on=["action_a"]
        ),
        ActionStatement(
            ref="action_c", action="core.action", args={}, depends_on=["action_b"]
        ),
        ActionStatement(
            ref="action_d", action="core.action", args={}, depends_on=["action_a"]
        ),
        ActionStatement(
            ref="action_e", action="core.action", args={}, depends_on=["action_d"]
        ),
        ActionStatement(
            ref="action_f",
            action="core.action",
            args={},
            depends_on=["action_c", "action_e"],
        ),
        ActionStatement(
            ref="action_g", action="core.action", args={}, depends_on=["action_f"]
        ),
    ]
    graph = RFGraph(rf_obj)
    dsl = react_flow_graph_to_dsl(graph, **metadata)
    assert dsl.actions == expected_wf_ir


def test_double_kite():
    r"""Double kite shape:

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
    """
    metadata = {"title": "Double kite", "description": "Test double kite shape"}
    rf_obj = {
        "nodes": [
            {
                "id": "a",
                "type": "core.action",
                "data": {"type": "core.action", "title": "action_a"},
            },
            {
                "id": "b",
                "type": "core.action",
                "data": {"type": "core.action", "title": "action_b"},
            },
            {
                "id": "c",
                "type": "core.action",
                "data": {"type": "core.action", "title": "action_c"},
            },
            {
                "id": "d",
                "type": "core.action",
                "data": {"type": "core.action", "title": "action_d"},
            },
            {
                "id": "e",
                "type": "core.action",
                "data": {"type": "core.action", "title": "action_e"},
            },
            {
                "id": "f",
                "type": "core.action",
                "data": {"type": "core.action", "title": "action_f"},
            },
            {
                "id": "g",
                "type": "core.action",
                "data": {"type": "core.action", "title": "action_g"},
            },
            {
                "id": "h",
                "type": "core.action",
                "data": {"type": "core.action", "title": "action_h"},
            },
            {
                "id": "i",
                "type": "core.action",
                "data": {"type": "core.action", "title": "action_i"},
            },
            {
                "id": "j",
                "type": "core.action",
                "data": {"type": "core.action", "title": "action_j"},
            },
            {
                "id": "k",
                "type": "core.action",
                "data": {"type": "core.action", "title": "action_k"},
            },
            {
                "id": "l",
                "type": "core.action",
                "data": {"type": "core.action", "title": "action_l"},
            },
            {
                "id": "m",
                "type": "core.action",
                "data": {"type": "core.action", "title": "action_m"},
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

    expected_wf_ir = [
        ActionStatement(ref="action_a", action="core.action", args={}),
        ActionStatement(
            ref="action_b", action="core.action", args={}, depends_on=["action_a"]
        ),
        ActionStatement(
            ref="action_c", action="core.action", args={}, depends_on=["action_b"]
        ),
        ActionStatement(
            ref="action_d", action="core.action", args={}, depends_on=["action_a"]
        ),
        ActionStatement(
            ref="action_e", action="core.action", args={}, depends_on=["action_d"]
        ),
        ActionStatement(
            ref="action_f",
            action="core.action",
            args={},
            depends_on=["action_c", "action_e"],
        ),
        ActionStatement(
            ref="action_g", action="core.action", args={}, depends_on=["action_f"]
        ),
        ActionStatement(
            ref="action_h", action="core.action", args={}, depends_on=["action_g"]
        ),
        ActionStatement(
            ref="action_i", action="core.action", args={}, depends_on=["action_g"]
        ),
        ActionStatement(
            ref="action_j", action="core.action", args={}, depends_on=["action_i"]
        ),
        ActionStatement(
            ref="action_k", action="core.action", args={}, depends_on=["action_h"]
        ),
        ActionStatement(
            ref="action_l", action="core.action", args={}, depends_on=["action_j"]
        ),
        ActionStatement(
            ref="action_m",
            action="core.action",
            args={},
            depends_on=["action_k", "action_l"],
        ),
    ]
    graph = RFGraph(rf_obj)
    dsl = react_flow_graph_to_dsl(graph, **metadata)
    assert dsl.actions == expected_wf_ir


def test_tree_1():
    r"""Tree 1 shape:

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
    """
    rf_obj = {
        "nodes": [
            {
                "id": "a",
                "type": "core.action",
                "data": {"type": "core.action", "title": "action_a"},
            },
            {
                "id": "b",
                "type": "core.action",
                "data": {"type": "core.action", "title": "action_b"},
            },
            {
                "id": "c",
                "type": "core.action",
                "data": {"type": "core.action", "title": "action_c"},
            },
            {
                "id": "d",
                "type": "core.action",
                "data": {"type": "core.action", "title": "action_d"},
            },
            {
                "id": "e",
                "type": "core.action",
                "data": {"type": "core.action", "title": "action_e"},
            },
            {
                "id": "f",
                "type": "core.action",
                "data": {"type": "core.action", "title": "action_f"},
            },
            {
                "id": "g",
                "type": "core.action",
                "data": {"type": "core.action", "title": "action_g"},
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

    expected_wf_ir = [
        ActionStatement(ref="action_a", action="core.action", args={}),
        ActionStatement(
            ref="action_b", action="core.action", args={}, depends_on=["action_a"]
        ),
        ActionStatement(
            ref="action_c", action="core.action", args={}, depends_on=["action_a"]
        ),
        ActionStatement(
            ref="action_d", action="core.action", args={}, depends_on=["action_b"]
        ),
        ActionStatement(
            ref="action_e", action="core.action", args={}, depends_on=["action_b"]
        ),
        ActionStatement(
            ref="action_f", action="core.action", args={}, depends_on=["action_c"]
        ),
        ActionStatement(
            ref="action_g", action="core.action", args={}, depends_on=["action_c"]
        ),
    ]
    metadata = {"title": "Tree 1", "description": "Test tree 1 shape"}
    graph = RFGraph(rf_obj)
    dsl = react_flow_graph_to_dsl(graph, **metadata)
    assert dsl.actions == expected_wf_ir


def test_tree_2():
    r"""Tree 2 shape:

         A
        / \
       B   E
      /|   |
     C D   F
           |
           G
    """
    rf_obj = {
        "nodes": [
            {
                "id": "a",
                "type": "core.action",
                "data": {"type": "core.action", "title": "action_a"},
            },
            {
                "id": "b",
                "type": "core.action",
                "data": {"type": "core.action", "title": "action_b"},
            },
            {
                "id": "c",
                "type": "core.action",
                "data": {"type": "core.action", "title": "action_c"},
            },
            {
                "id": "d",
                "type": "core.action",
                "data": {"type": "core.action", "title": "action_d"},
            },
            {
                "id": "e",
                "type": "core.action",
                "data": {"type": "core.action", "title": "action_e"},
            },
            {
                "id": "f",
                "type": "core.action",
                "data": {"type": "core.action", "title": "action_f"},
            },
            {
                "id": "g",
                "type": "core.action",
                "data": {"type": "core.action", "title": "action_g"},
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

    expected_wf_ir = [
        ActionStatement(ref="action_a", action="core.action", args={}),
        ActionStatement(
            ref="action_b", action="core.action", args={}, depends_on=["action_a"]
        ),
        ActionStatement(
            ref="action_c", action="core.action", args={}, depends_on=["action_b"]
        ),
        ActionStatement(
            ref="action_d", action="core.action", args={}, depends_on=["action_b"]
        ),
        ActionStatement(
            ref="action_e", action="core.action", args={}, depends_on=["action_a"]
        ),
        ActionStatement(
            ref="action_f", action="core.action", args={}, depends_on=["action_e"]
        ),
        ActionStatement(
            ref="action_g", action="core.action", args={}, depends_on=["action_f"]
        ),
    ]
    metadata = {"title": "Tree 2", "description": "Test tree 2 shape"}
    graph = RFGraph(rf_obj)
    dsl = react_flow_graph_to_dsl(graph, **metadata)
    assert dsl.actions == expected_wf_ir


def test_complex_dag_1():
    r"""Complex DAG shape:

         A
        / \
       B   C
      / \ / \
     D   E   F
      \  |  /
       \ | /
         G

    """
    rf_obj = {
        "nodes": [
            {
                "id": "a",
                "type": "core.action",
                "data": {"type": "core.action", "title": "action_a"},
            },
            {
                "id": "b",
                "type": "core.action",
                "data": {"type": "core.action", "title": "action_b"},
            },
            {
                "id": "c",
                "type": "core.action",
                "data": {"type": "core.action", "title": "action_c"},
            },
            {
                "id": "d",
                "type": "core.action",
                "data": {"type": "core.action", "title": "action_d"},
            },
            {
                "id": "e",
                "type": "core.action",
                "data": {"type": "core.action", "title": "action_e"},
            },
            {
                "id": "f",
                "type": "core.action",
                "data": {"type": "core.action", "title": "action_f"},
            },
            {
                "id": "g",
                "type": "core.action",
                "data": {"type": "core.action", "title": "action_g"},
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

    expected_wf_ir = [
        ActionStatement(ref="action_a", action="core.action", args={}),
        ActionStatement(
            ref="action_b", action="core.action", args={}, depends_on=["action_a"]
        ),
        ActionStatement(
            ref="action_c", action="core.action", args={}, depends_on=["action_a"]
        ),
        ActionStatement(
            ref="action_d", action="core.action", args={}, depends_on=["action_b"]
        ),
        ActionStatement(
            ref="action_e",
            action="core.action",
            args={},
            depends_on=["action_b", "action_c"],
        ),
        ActionStatement(
            ref="action_f", action="core.action", args={}, depends_on=["action_c"]
        ),
        ActionStatement(
            ref="action_g",
            action="core.action",
            args={},
            depends_on=["action_d", "action_e", "action_f"],
        ),
    ]
    metadata = {"title": "Complex DAG 1", "description": "Test complex DAG 1 shape"}
    graph = RFGraph(rf_obj)
    dsl = react_flow_graph_to_dsl(graph, **metadata)
    assert dsl.actions == expected_wf_ir


def test_complex_dag_2():
    r"""Complex DAG shape:

         A
        / \
       B   C
      / \ / \
     D   E   F
      \ / \ /
       G   H
        \ /
         I

    """
    rf_obj = {
        "nodes": [
            {
                "id": "a",
                "type": "core.action",
                "data": {"type": "core.action", "title": "action_a"},
            },
            {
                "id": "b",
                "type": "core.action",
                "data": {"type": "core.action", "title": "action_b"},
            },
            {
                "id": "c",
                "type": "core.action",
                "data": {"type": "core.action", "title": "action_c"},
            },
            {
                "id": "d",
                "type": "core.action",
                "data": {"type": "core.action", "title": "action_d"},
            },
            {
                "id": "e",
                "type": "core.action",
                "data": {"type": "core.action", "title": "action_e"},
            },
            {
                "id": "f",
                "type": "core.action",
                "data": {"type": "core.action", "title": "action_f"},
            },
            {
                "id": "g",
                "type": "core.action",
                "data": {"type": "core.action", "title": "action_g"},
            },
            {
                "id": "h",
                "type": "core.action",
                "data": {"type": "core.action", "title": "action_h"},
            },
            {
                "id": "i",
                "type": "core.action",
                "data": {"type": "core.action", "title": "action_i"},
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

    expected_wf_ir = [
        ActionStatement(ref="action_a", action="core.action", args={}),
        ActionStatement(
            ref="action_b", action="core.action", args={}, depends_on=["action_a"]
        ),
        ActionStatement(
            ref="action_c", action="core.action", args={}, depends_on=["action_a"]
        ),
        ActionStatement(
            ref="action_d", action="core.action", args={}, depends_on=["action_b"]
        ),
        ActionStatement(
            ref="action_e",
            action="core.action",
            args={},
            depends_on=["action_b", "action_c"],
        ),
        ActionStatement(
            ref="action_f", action="core.action", args={}, depends_on=["action_c"]
        ),
        ActionStatement(
            ref="action_g",
            action="core.action",
            args={},
            depends_on=["action_d", "action_e"],
        ),
        ActionStatement(
            ref="action_h",
            action="core.action",
            args={},
            depends_on=["action_e", "action_f"],
        ),
        ActionStatement(
            ref="action_i",
            action="core.action",
            args={},
            depends_on=["action_g", "action_h"],
        ),
    ]
    metadata = {"title": "Complex DAG 2", "description": "Test complex DAG 2 shape"}
    graph = RFGraph(rf_obj)
    dsl = react_flow_graph_to_dsl(graph, **metadata)
    assert dsl.actions == expected_wf_ir


def complex_dag_3():
    r"""Complex DAG shape:

         A
        / \
       B   \
      / \   \
     C   D   E - F
      \ /   / \   \
       G   H   I - J
        \ /        |
         K         L

    """
    rf_obj = {
        "nodes": [
            {
                "id": "a",
                "type": "core.action",
                "data": {"type": "core.action", "title": "action_a"},
            },
            {
                "id": "b",
                "type": "core.action",
                "data": {"type": "core.action", "title": "action_b"},
            },
            {
                "id": "c",
                "type": "core.action",
                "data": {"type": "core.action", "title": "action_c"},
            },
            {
                "id": "d",
                "type": "core.action",
                "data": {"type": "core.action", "title": "action_d"},
            },
            {
                "id": "e",
                "type": "core.action",
                "data": {"type": "core.action", "title": "action_e"},
            },
            {
                "id": "f",
                "type": "core.action",
                "data": {"type": "core.action", "title": "action_f"},
            },
            {
                "id": "g",
                "type": "core.action",
                "data": {"type": "core.action", "title": "action_g"},
            },
            {
                "id": "h",
                "type": "core.action",
                "data": {"type": "core.action", "title": "action_h"},
            },
            {
                "id": "i",
                "type": "core.action",
                "data": {"type": "core.action", "title": "action_i"},
            },
            {
                "id": "j",
                "type": "core.action",
                "data": {"type": "core.action", "title": "action_j"},
            },
            {
                "id": "k",
                "type": "core.action",
                "data": {"type": "core.action", "title": "action_k"},
            },
            {
                "id": "l",
                "type": "core.action",
                "data": {"type": "core.action", "title": "action_l"},
            },
        ],
        "edges": [
            {"id": "a_b", "source": "a", "target": "b"},
            {"id": "a_e", "source": "a", "target": "e"},
            {"id": "b_c", "source": "b", "target": "c"},
            {"id": "b_d", "source": "b", "target": "d"},
            {"id": "c_g", "source": "c", "target": "g"},
            {"id": "d_g", "source": "d", "target": "g"},
            {"id": "e_f", "source": "e", "target": "f"},
            {"id": "e_i", "source": "e", "target": "i"},
            {"id": "f_j", "source": "f", "target": "j"},
            {"id": "g_k", "source": "g", "target": "k"},
            {"id": "h_k", "source": "h", "target": "k"},
            {"id": "i_j", "source": "i", "target": "j"},
            {"id": "j_l", "source": "j", "target": "l"},
        ],
    }

    expected_wf_ir = [
        ActionStatement(ref="action_a", action="core.action", args={}),
        ActionStatement(
            ref="action_b", action="core.action", args={}, depends_on=["action_a"]
        ),
        ActionStatement(
            ref="action_c", action="core.action", args={}, depends_on=["action_b"]
        ),
        ActionStatement(
            ref="action_d", action="core.action", args={}, depends_on=["action_b"]
        ),
        ActionStatement(
            ref="action_e", action="core.action", args={}, depends_on=["action_a"]
        ),
        ActionStatement(
            ref="action_f", action="core.action", args={}, depends_on=["action_e"]
        ),
        ActionStatement(
            ref="action_g",
            action="core.action",
            args={},
            depends_on=["action_c", "action_d"],
        ),
        ActionStatement(
            ref="action_h", action="core.action", args={}, depends_on=["action_e"]
        ),
        ActionStatement(
            ref="action_i", action="core.action", args={}, depends_on=["action_e"]
        ),
        ActionStatement(
            ref="action_j",
            action="core.action",
            args={},
            depends_on=["action_f", "action_i"],
        ),
        ActionStatement(
            ref="action_k",
            action="core.action",
            args={},
            depends_on=["action_g", "action_h"],
        ),
        ActionStatement(
            ref="action_l", action="core.action", args={}, depends_on=["action_j"]
        ),
    ]
    metadata = {"title": "Complex DAG 3", "description": "Test complex DAG 3 shape"}
    graph = RFGraph(rf_obj)
    dsl = react_flow_graph_to_dsl(graph, **metadata)
    assert dsl.actions == expected_wf_ir
