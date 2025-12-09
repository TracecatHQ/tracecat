import pytest
from pydantic import BaseModel

from tracecat.dsl.common import DSLEntrypoint, DSLInput
from tracecat.dsl.schemas import ActionStatement
from tracecat.dsl.view import RFGraph, TriggerNode, TriggerNodeData


@pytest.fixture(scope="session")
def metadata():
    class TestMetadata(BaseModel):
        title: str
        description: str
        entrypoint: DSLEntrypoint
        trigger: TriggerNode

    metadata = TestMetadata(
        title="TEST_WORKFLOW",
        description="TEST_DESCRIPTION",
        entrypoint=DSLEntrypoint(ref="action_a"),
        trigger=TriggerNode(
            id="trigger-TEST_WORKFLOW_ID",
            type="trigger",
            data=TriggerNodeData(
                title="Trigger",
                status="online",
                is_configured=True,
                webhook={},  # Empty dict for webhook
            ),
        ),
    )

    return metadata


def build_actions(graph: RFGraph) -> list[ActionStatement]:
    # Use node ID as ref for testing purposes
    return [
        ActionStatement(
            ref=node.id,
            action=node.data.type,
            args=node.data.args,  # For testing convenience
            depends_on=sorted(
                graph.node_map[nid].id for nid in graph.dep_list[node.id]
            ),
        )
        for node in graph.action_nodes()
    ]


def test_parse_dag_simple_sequence(metadata):
    """Simple sequence

    trigger -> A -> B -> C

    Checks:
    1. The sequence is correctly parsed
    2. The depends_on field is correctly set
    3. Action refs (slugs) are correfly constructed
    """

    rf_obj = {
        "nodes": [
            metadata.trigger,
            {
                "id": "a",
                "type": "udf",
                "data": {
                    "type": "udf",
                    "title": "Action A",
                    "args": {"test": 1},
                },
            },
            {
                "id": "b",
                "type": "udf",
                "data": {
                    "type": "udf",
                    "title": "Action B",
                    "args": {"test": 2},
                },
            },
            {
                "id": "c",
                "type": "udf",
                "data": {
                    "type": "udf",
                    "title": "Action C",
                    "args": {"test": 3},
                },
            },
        ],
        "edges": [
            {"id": "trigger_edge", "source": metadata.trigger.id, "target": "a"},
            {"id": "a_b", "source": "a", "target": "b"},
            {"id": "b_c", "source": "b", "target": "c"},
        ],
    }

    expected_wf_ir = [
        ActionStatement(
            ref="a",
            action="udf",
            args={"test": 1},
        ),
        ActionStatement(
            ref="b",
            action="udf",
            args={"test": 2},
            depends_on=["a"],
        ),
        ActionStatement(
            ref="c",
            action="udf",
            args={"test": 3},
            depends_on=["b"],
        ),
    ]
    graph = RFGraph.model_validate(rf_obj)
    stmts = build_actions(graph)
    dsl = DSLInput(actions=stmts, **metadata.model_dump())
    assert dsl.actions == expected_wf_ir


def test_kite(metadata):
    r"""Kite shape:

       trigger -> A
                  /\
                 B  D
                 |  |
                 C  E
                  \/
                   F
                   |
                   G

    """
    rf_obj = {
        "nodes": [
            metadata.trigger,
            {
                "id": "a",
                "type": "udf",
                "data": {"type": "udf", "title": "action_a"},
            },
            {
                "id": "b",
                "type": "udf",
                "data": {"type": "udf", "title": "action_b"},
            },
            {
                "id": "c",
                "type": "udf",
                "data": {"type": "udf", "title": "action_c"},
            },
            {
                "id": "d",
                "type": "udf",
                "data": {"type": "udf", "title": "action_d"},
            },
            {
                "id": "e",
                "type": "udf",
                "data": {"type": "udf", "title": "action_e"},
            },
            {
                "id": "f",
                "type": "udf",
                "data": {"type": "udf", "title": "action_f"},
            },
            {
                "id": "g",
                "type": "udf",
                "data": {"type": "udf", "title": "action_g"},
            },
        ],
        "edges": [
            {"id": "trigger_edge", "source": metadata.trigger.id, "target": "a"},
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
        ActionStatement(ref="a", action="udf", args={}),
        ActionStatement(ref="b", action="udf", args={}, depends_on=["a"]),
        ActionStatement(ref="c", action="udf", args={}, depends_on=["b"]),
        ActionStatement(ref="d", action="udf", args={}, depends_on=["a"]),
        ActionStatement(ref="e", action="udf", args={}, depends_on=["d"]),
        ActionStatement(
            ref="f",
            action="udf",
            args={},
            depends_on=["c", "e"],
        ),
        ActionStatement(ref="g", action="udf", args={}, depends_on=["f"]),
    ]
    graph = RFGraph.model_validate(rf_obj)
    stmts = build_actions(graph)
    dsl = DSLInput(actions=stmts, **metadata.model_dump())
    assert dsl.actions == expected_wf_ir


def test_double_kite(metadata):
    r"""Double kite shape:

       trigger -> A
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
    rf_obj = {
        "nodes": [
            metadata.trigger,
            {
                "id": "a",
                "type": "udf",
                "data": {"type": "udf", "title": "action_a"},
            },
            {
                "id": "b",
                "type": "udf",
                "data": {"type": "udf", "title": "action_b"},
            },
            {
                "id": "c",
                "type": "udf",
                "data": {"type": "udf", "title": "action_c"},
            },
            {
                "id": "d",
                "type": "udf",
                "data": {"type": "udf", "title": "action_d"},
            },
            {
                "id": "e",
                "type": "udf",
                "data": {"type": "udf", "title": "action_e"},
            },
            {
                "id": "f",
                "type": "udf",
                "data": {"type": "udf", "title": "action_f"},
            },
            {
                "id": "g",
                "type": "udf",
                "data": {"type": "udf", "title": "action_g"},
            },
            {
                "id": "h",
                "type": "udf",
                "data": {"type": "udf", "title": "action_h"},
            },
            {
                "id": "i",
                "type": "udf",
                "data": {"type": "udf", "title": "action_i"},
            },
            {
                "id": "j",
                "type": "udf",
                "data": {"type": "udf", "title": "action_j"},
            },
            {
                "id": "k",
                "type": "udf",
                "data": {"type": "udf", "title": "action_k"},
            },
            {
                "id": "l",
                "type": "udf",
                "data": {"type": "udf", "title": "action_l"},
            },
            {
                "id": "m",
                "type": "udf",
                "data": {"type": "udf", "title": "action_m"},
            },
        ],
        "edges": [
            {"id": "trigger_edge", "source": metadata.trigger.id, "target": "a"},
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
        ActionStatement(ref="a", action="udf", args={}),
        ActionStatement(ref="b", action="udf", args={}, depends_on=["a"]),
        ActionStatement(ref="c", action="udf", args={}, depends_on=["b"]),
        ActionStatement(ref="d", action="udf", args={}, depends_on=["a"]),
        ActionStatement(ref="e", action="udf", args={}, depends_on=["d"]),
        ActionStatement(
            ref="f",
            action="udf",
            args={},
            depends_on=["c", "e"],
        ),
        ActionStatement(ref="g", action="udf", args={}, depends_on=["f"]),
        ActionStatement(ref="h", action="udf", args={}, depends_on=["g"]),
        ActionStatement(ref="i", action="udf", args={}, depends_on=["g"]),
        ActionStatement(ref="j", action="udf", args={}, depends_on=["i"]),
        ActionStatement(ref="k", action="udf", args={}, depends_on=["h"]),
        ActionStatement(ref="l", action="udf", args={}, depends_on=["j"]),
        ActionStatement(
            ref="m",
            action="udf",
            args={},
            depends_on=["k", "l"],
        ),
    ]
    graph = RFGraph.model_validate(rf_obj)
    stmts = build_actions(graph)
    dsl = DSLInput(actions=stmts, **metadata.model_dump())
    assert dsl.actions == expected_wf_ir


def test_tree_1(metadata):
    r"""Tree 1 shape:

       trigger -> A
                  /\
                 B  C
                /|  |\
               D E  F G

    """
    rf_obj = {
        "nodes": [
            metadata.trigger,
            {
                "id": "a",
                "type": "udf",
                "data": {"type": "udf", "title": "action_a"},
            },
            {
                "id": "b",
                "type": "udf",
                "data": {"type": "udf", "title": "action_b"},
            },
            {
                "id": "c",
                "type": "udf",
                "data": {"type": "udf", "title": "action_c"},
            },
            {
                "id": "d",
                "type": "udf",
                "data": {"type": "udf", "title": "action_d"},
            },
            {
                "id": "e",
                "type": "udf",
                "data": {"type": "udf", "title": "action_e"},
            },
            {
                "id": "f",
                "type": "udf",
                "data": {"type": "udf", "title": "action_f"},
            },
            {
                "id": "g",
                "type": "udf",
                "data": {"type": "udf", "title": "action_g"},
            },
        ],
        "edges": [
            {"id": "trigger_edge", "source": metadata.trigger.id, "target": "a"},
            {"id": "a_b", "source": "a", "target": "b"},
            {"id": "a_c", "source": "a", "target": "c"},
            {"id": "b_d", "source": "b", "target": "d"},
            {"id": "b_e", "source": "b", "target": "e"},
            {"id": "c_f", "source": "c", "target": "f"},
            {"id": "c_g", "source": "c", "target": "g"},
        ],
    }

    expected_wf_ir = [
        ActionStatement(ref="a", action="udf", args={}),
        ActionStatement(ref="b", action="udf", args={}, depends_on=["a"]),
        ActionStatement(ref="c", action="udf", args={}, depends_on=["a"]),
        ActionStatement(ref="d", action="udf", args={}, depends_on=["b"]),
        ActionStatement(ref="e", action="udf", args={}, depends_on=["b"]),
        ActionStatement(ref="f", action="udf", args={}, depends_on=["c"]),
        ActionStatement(ref="g", action="udf", args={}, depends_on=["c"]),
    ]
    graph = RFGraph.model_validate(rf_obj)
    stmts = build_actions(graph)
    dsl = DSLInput(actions=stmts, **metadata.model_dump())
    assert dsl.actions == expected_wf_ir


def test_tree_2(metadata):
    r"""Tree 2 shape:

         trigger -> A
                    / \
                   B   E
                  /|   |
                 C D   F
                       |
                       G
    """
    rf_obj = {
        "nodes": [
            metadata.trigger,
            {
                "id": "a",
                "type": "udf",
                "data": {"type": "udf", "title": "action_a"},
            },
            {
                "id": "b",
                "type": "udf",
                "data": {"type": "udf", "title": "action_b"},
            },
            {
                "id": "c",
                "type": "udf",
                "data": {"type": "udf", "title": "action_c"},
            },
            {
                "id": "d",
                "type": "udf",
                "data": {"type": "udf", "title": "action_d"},
            },
            {
                "id": "e",
                "type": "udf",
                "data": {"type": "udf", "title": "action_e"},
            },
            {
                "id": "f",
                "type": "udf",
                "data": {"type": "udf", "title": "action_f"},
            },
            {
                "id": "g",
                "type": "udf",
                "data": {"type": "udf", "title": "action_g"},
            },
        ],
        "edges": [
            {"id": "trigger_edge", "source": metadata.trigger.id, "target": "a"},
            {"id": "a_b", "source": "a", "target": "b"},
            {"id": "a_e", "source": "a", "target": "e"},
            {"id": "b_c", "source": "b", "target": "c"},
            {"id": "b_d", "source": "b", "target": "d"},
            {"id": "e_f", "source": "e", "target": "f"},
            {"id": "f_g", "source": "f", "target": "g"},
        ],
    }

    expected_wf_ir = [
        ActionStatement(ref="a", action="udf", args={}),
        ActionStatement(ref="b", action="udf", args={}, depends_on=["a"]),
        ActionStatement(ref="c", action="udf", args={}, depends_on=["b"]),
        ActionStatement(ref="d", action="udf", args={}, depends_on=["b"]),
        ActionStatement(ref="e", action="udf", args={}, depends_on=["a"]),
        ActionStatement(ref="f", action="udf", args={}, depends_on=["e"]),
        ActionStatement(ref="g", action="udf", args={}, depends_on=["f"]),
    ]
    graph = RFGraph.model_validate(rf_obj)
    stmts = build_actions(graph)
    dsl = DSLInput(actions=stmts, **metadata.model_dump())
    assert dsl.actions == expected_wf_ir


def test_complex_dag_1(metadata):
    r"""Complex DAG shape:

         trigger -> A
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
            metadata.trigger,
            {
                "id": "a",
                "type": "udf",
                "data": {"type": "udf", "title": "action_a"},
            },
            {
                "id": "b",
                "type": "udf",
                "data": {"type": "udf", "title": "action_b"},
            },
            {
                "id": "c",
                "type": "udf",
                "data": {"type": "udf", "title": "action_c"},
            },
            {
                "id": "d",
                "type": "udf",
                "data": {"type": "udf", "title": "action_d"},
            },
            {
                "id": "e",
                "type": "udf",
                "data": {"type": "udf", "title": "action_e"},
            },
            {
                "id": "f",
                "type": "udf",
                "data": {"type": "udf", "title": "action_f"},
            },
            {
                "id": "g",
                "type": "udf",
                "data": {"type": "udf", "title": "action_g"},
            },
        ],
        "edges": [
            {"id": "trigger_edge", "source": metadata.trigger.id, "target": "a"},
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
        ActionStatement(ref="a", action="udf", args={}),
        ActionStatement(ref="b", action="udf", args={}, depends_on=["a"]),
        ActionStatement(ref="c", action="udf", args={}, depends_on=["a"]),
        ActionStatement(ref="d", action="udf", args={}, depends_on=["b"]),
        ActionStatement(
            ref="e",
            action="udf",
            args={},
            depends_on=["b", "c"],
        ),
        ActionStatement(ref="f", action="udf", args={}, depends_on=["c"]),
        ActionStatement(
            ref="g",
            action="udf",
            args={},
            depends_on=["d", "e", "f"],
        ),
    ]
    graph = RFGraph.model_validate(rf_obj)
    stmts = build_actions(graph)
    dsl = DSLInput(actions=stmts, **metadata.model_dump())
    assert dsl.actions == expected_wf_ir


def test_complex_dag_2(metadata):
    r"""Complex DAG shape:

         trigger -> A
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
            metadata.trigger,
            {
                "id": "a",
                "type": "udf",
                "data": {"type": "udf", "title": "action_a"},
            },
            {
                "id": "b",
                "type": "udf",
                "data": {"type": "udf", "title": "action_b"},
            },
            {
                "id": "c",
                "type": "udf",
                "data": {"type": "udf", "title": "action_c"},
            },
            {
                "id": "d",
                "type": "udf",
                "data": {"type": "udf", "title": "action_d"},
            },
            {
                "id": "e",
                "type": "udf",
                "data": {"type": "udf", "title": "action_e"},
            },
            {
                "id": "f",
                "type": "udf",
                "data": {"type": "udf", "title": "action_f"},
            },
            {
                "id": "g",
                "type": "udf",
                "data": {"type": "udf", "title": "action_g"},
            },
            {
                "id": "h",
                "type": "udf",
                "data": {"type": "udf", "title": "action_h"},
            },
            {
                "id": "i",
                "type": "udf",
                "data": {"type": "udf", "title": "action_i"},
            },
        ],
        "edges": [
            {"id": "trigger_edge", "source": metadata.trigger.id, "target": "a"},
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
        ActionStatement(ref="a", action="udf", args={}),
        ActionStatement(ref="b", action="udf", args={}, depends_on=["a"]),
        ActionStatement(ref="c", action="udf", args={}, depends_on=["a"]),
        ActionStatement(ref="d", action="udf", args={}, depends_on=["b"]),
        ActionStatement(
            ref="e",
            action="udf",
            args={},
            depends_on=["b", "c"],
        ),
        ActionStatement(ref="f", action="udf", args={}, depends_on=["c"]),
        ActionStatement(
            ref="g",
            action="udf",
            args={},
            depends_on=["d", "e"],
        ),
        ActionStatement(
            ref="h",
            action="udf",
            args={},
            depends_on=["e", "f"],
        ),
        ActionStatement(
            ref="i",
            action="udf",
            args={},
            depends_on=["g", "h"],
        ),
    ]
    graph = RFGraph.model_validate(rf_obj)
    stmts = build_actions(graph)
    dsl = DSLInput(actions=stmts, **metadata.model_dump())
    assert dsl.actions == expected_wf_ir


def test_complex_dag_3(metadata):
    r"""Complex DAG shape:

         trigger -> A
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
            metadata.trigger,
            {
                "id": "a",
                "type": "udf",
                "data": {"type": "udf", "title": "action_a"},
            },
            {
                "id": "b",
                "type": "udf",
                "data": {"type": "udf", "title": "action_b"},
            },
            {
                "id": "c",
                "type": "udf",
                "data": {"type": "udf", "title": "action_c"},
            },
            {
                "id": "d",
                "type": "udf",
                "data": {"type": "udf", "title": "action_d"},
            },
            {
                "id": "e",
                "type": "udf",
                "data": {"type": "udf", "title": "action_e"},
            },
            {
                "id": "f",
                "type": "udf",
                "data": {"type": "udf", "title": "action_f"},
            },
            {
                "id": "g",
                "type": "udf",
                "data": {"type": "udf", "title": "action_g"},
            },
            {
                "id": "h",
                "type": "udf",
                "data": {"type": "udf", "title": "action_h"},
            },
            {
                "id": "i",
                "type": "udf",
                "data": {"type": "udf", "title": "action_i"},
            },
            {
                "id": "j",
                "type": "udf",
                "data": {"type": "udf", "title": "action_j"},
            },
            {
                "id": "k",
                "type": "udf",
                "data": {"type": "udf", "title": "action_k"},
            },
            {
                "id": "l",
                "type": "udf",
                "data": {"type": "udf", "title": "action_l"},
            },
        ],
        "edges": [
            {"id": "trigger_edge", "source": metadata.trigger.id, "target": "a"},
            {"id": "a_b", "source": "a", "target": "b"},
            {"id": "a_e", "source": "a", "target": "e"},
            {"id": "b_c", "source": "b", "target": "c"},
            {"id": "b_d", "source": "b", "target": "d"},
            {"id": "c_g", "source": "c", "target": "g"},
            {"id": "d_g", "source": "d", "target": "g"},
            {"id": "e_f", "source": "e", "target": "f"},
            {"id": "e_i", "source": "e", "target": "i"},
            {"id": "e_h", "source": "e", "target": "h"},
            {"id": "f_j", "source": "f", "target": "j"},
            {"id": "g_k", "source": "g", "target": "k"},
            {"id": "h_k", "source": "h", "target": "k"},
            {"id": "i_j", "source": "i", "target": "j"},
            {"id": "j_l", "source": "j", "target": "l"},
        ],
    }

    expected_wf_ir = [
        ActionStatement(ref="a", action="udf", args={}),
        ActionStatement(ref="b", action="udf", args={}, depends_on=["a"]),
        ActionStatement(ref="c", action="udf", args={}, depends_on=["b"]),
        ActionStatement(ref="d", action="udf", args={}, depends_on=["b"]),
        ActionStatement(ref="e", action="udf", args={}, depends_on=["a"]),
        ActionStatement(ref="f", action="udf", args={}, depends_on=["e"]),
        ActionStatement(
            ref="g",
            action="udf",
            args={},
            depends_on=["c", "d"],
        ),
        ActionStatement(ref="h", action="udf", args={}, depends_on=["e"]),
        ActionStatement(ref="i", action="udf", args={}, depends_on=["e"]),
        ActionStatement(
            ref="j",
            action="udf",
            args={},
            depends_on=["f", "i"],
        ),
        ActionStatement(
            ref="k",
            action="udf",
            args={},
            depends_on=["g", "h"],
        ),
        ActionStatement(ref="l", action="udf", args={}, depends_on=["j"]),
    ]
    graph = RFGraph.model_validate(rf_obj)
    stmts = build_actions(graph)
    dsl = DSLInput(actions=stmts, **metadata.model_dump())
    assert dsl.actions == expected_wf_ir
