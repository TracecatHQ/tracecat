from tracecat.experimental.dsl.graph import RFGraph, parse_dag_to_blocks


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
    dsl = parse_dag_to_blocks(RFGraph(rf_obj))
    assert dsl.root.model_dump() == expected_wf_ir


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
    assert dsl.root.model_dump() == expected_wf_ir


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
    assert dsl.root.model_dump() == expected_wf_ir
