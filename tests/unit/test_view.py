import uuid

import pytest
from pydantic import BaseModel

from tracecat.dsl.common import DSLEntrypoint, DSLInput
from tracecat.dsl.schemas import ActionStatement
from tracecat.dsl.view import RFGraph, TriggerNode, TriggerNodeData

# Fixed UUIDs for testing - deterministic for reproducibility
WORKFLOW_UUID = uuid.UUID("00000000-0000-0000-0000-000000000000")
UUID_A = uuid.UUID("00000000-0000-0000-0000-00000000000a")
UUID_B = uuid.UUID("00000000-0000-0000-0000-00000000000b")
UUID_C = uuid.UUID("00000000-0000-0000-0000-00000000000c")
UUID_D = uuid.UUID("00000000-0000-0000-0000-00000000000d")
UUID_E = uuid.UUID("00000000-0000-0000-0000-00000000000e")
UUID_F = uuid.UUID("00000000-0000-0000-0000-00000000000f")
UUID_G = uuid.UUID("00000000-0000-0000-0000-000000000010")
UUID_H = uuid.UUID("00000000-0000-0000-0000-000000000011")
UUID_I = uuid.UUID("00000000-0000-0000-0000-000000000012")
UUID_J = uuid.UUID("00000000-0000-0000-0000-000000000013")
UUID_K = uuid.UUID("00000000-0000-0000-0000-000000000014")
UUID_L = uuid.UUID("00000000-0000-0000-0000-000000000015")
UUID_M = uuid.UUID("00000000-0000-0000-0000-000000000016")


@pytest.fixture(scope="session")
def metadata():
    class TestMetadata(BaseModel):
        title: str
        description: str
        entrypoint: DSLEntrypoint
        trigger: TriggerNode

    trigger_id = f"trigger-{WORKFLOW_UUID}"
    metadata = TestMetadata(
        title="TEST_WORKFLOW",
        description="TEST_DESCRIPTION",
        entrypoint=DSLEntrypoint(ref="action_a"),
        trigger=TriggerNode(
            id=trigger_id,
            type="trigger",
            data=TriggerNodeData(
                title="Trigger",
                status="online",
                is_configured=True,
                webhook={},
            ),
        ),
    )

    return metadata


def uuid_to_ref(node_id: str) -> str:
    """Convert node UUID to ref by taking the last hex character."""
    # UUIDs like "00000000-0000-0000-0000-00000000000a" -> "a"
    # UUIDs like "00000000-0000-0000-0000-000000000010" -> "g" (hex 10 = 16 -> 'g')
    last_hex = node_id[-2:]  # Last 2 hex chars
    num = int(last_hex, 16)
    # Map 10->g, 11->h, etc. (a=10 in our UUID scheme, so offset by 10)
    if num < 10:
        return chr(ord("a") + num)
    return chr(ord("a") + num - 10)


def build_actions(graph: RFGraph) -> list[ActionStatement]:
    # Build a mapping from node ID to ref for dependency resolution
    id_to_ref = {node.id: uuid_to_ref(str(node.id)) for node in graph.action_nodes()}
    return [
        ActionStatement(
            ref=id_to_ref[node.id],
            action=node.data.type,
            args=node.data.args,
            depends_on=sorted(id_to_ref[nid] for nid in graph.dep_list[node.id]),
        )
        for node in graph.action_nodes()
    ]


def test_parse_dag_simple_sequence(metadata):
    """Simple sequence

    trigger -> A -> B -> C

    Checks:
    1. The sequence is correctly parsed
    2. The depends_on field is correctly set
    3. Action refs (slugs) are correctly constructed
    """

    rf_obj = {
        "nodes": [
            metadata.trigger,
            {
                "id": str(UUID_A),
                "type": "udf",
                "data": {
                    "type": "udf",
                    "args": {"test": 1},
                },
            },
            {
                "id": str(UUID_B),
                "type": "udf",
                "data": {
                    "type": "udf",
                    "args": {"test": 2},
                },
            },
            {
                "id": str(UUID_C),
                "type": "udf",
                "data": {
                    "type": "udf",
                    "args": {"test": 3},
                },
            },
        ],
        "edges": [
            {
                "id": "trigger_edge",
                "source": metadata.trigger.id,
                "target": str(UUID_A),
            },
            {"id": "a_b", "source": str(UUID_A), "target": str(UUID_B)},
            {"id": "b_c", "source": str(UUID_B), "target": str(UUID_C)},
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
                "id": str(UUID_A),
                "type": "udf",
                "data": {"type": "udf"},
            },
            {
                "id": str(UUID_B),
                "type": "udf",
                "data": {"type": "udf"},
            },
            {
                "id": str(UUID_C),
                "type": "udf",
                "data": {"type": "udf"},
            },
            {
                "id": str(UUID_D),
                "type": "udf",
                "data": {"type": "udf"},
            },
            {
                "id": str(UUID_E),
                "type": "udf",
                "data": {"type": "udf"},
            },
            {
                "id": str(UUID_F),
                "type": "udf",
                "data": {"type": "udf"},
            },
            {
                "id": str(UUID_G),
                "type": "udf",
                "data": {"type": "udf"},
            },
        ],
        "edges": [
            {
                "id": "trigger_edge",
                "source": metadata.trigger.id,
                "target": str(UUID_A),
            },
            {"id": "a_b", "source": str(UUID_A), "target": str(UUID_B)},
            {"id": "b_c", "source": str(UUID_B), "target": str(UUID_C)},
            {"id": "c_f", "source": str(UUID_C), "target": str(UUID_F)},
            {"id": "a_d", "source": str(UUID_A), "target": str(UUID_D)},
            {"id": "d_e", "source": str(UUID_D), "target": str(UUID_E)},
            {"id": "e_f", "source": str(UUID_E), "target": str(UUID_F)},
            {"id": "f_g", "source": str(UUID_F), "target": str(UUID_G)},
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
                "id": str(UUID_A),
                "type": "udf",
                "data": {"type": "udf"},
            },
            {
                "id": str(UUID_B),
                "type": "udf",
                "data": {"type": "udf"},
            },
            {
                "id": str(UUID_C),
                "type": "udf",
                "data": {"type": "udf"},
            },
            {
                "id": str(UUID_D),
                "type": "udf",
                "data": {"type": "udf"},
            },
            {
                "id": str(UUID_E),
                "type": "udf",
                "data": {"type": "udf"},
            },
            {
                "id": str(UUID_F),
                "type": "udf",
                "data": {"type": "udf"},
            },
            {
                "id": str(UUID_G),
                "type": "udf",
                "data": {"type": "udf"},
            },
            {
                "id": str(UUID_H),
                "type": "udf",
                "data": {"type": "udf"},
            },
            {
                "id": str(UUID_I),
                "type": "udf",
                "data": {"type": "udf"},
            },
            {
                "id": str(UUID_J),
                "type": "udf",
                "data": {"type": "udf"},
            },
            {
                "id": str(UUID_K),
                "type": "udf",
                "data": {"type": "udf"},
            },
            {
                "id": str(UUID_L),
                "type": "udf",
                "data": {"type": "udf"},
            },
            {
                "id": str(UUID_M),
                "type": "udf",
                "data": {"type": "udf"},
            },
        ],
        "edges": [
            {
                "id": "trigger_edge",
                "source": metadata.trigger.id,
                "target": str(UUID_A),
            },
            {"id": "a_b", "source": str(UUID_A), "target": str(UUID_B)},
            {"id": "b_c", "source": str(UUID_B), "target": str(UUID_C)},
            {"id": "c_f", "source": str(UUID_C), "target": str(UUID_F)},
            {"id": "a_d", "source": str(UUID_A), "target": str(UUID_D)},
            {"id": "d_e", "source": str(UUID_D), "target": str(UUID_E)},
            {"id": "e_f", "source": str(UUID_E), "target": str(UUID_F)},
            {"id": "f_g", "source": str(UUID_F), "target": str(UUID_G)},
            {"id": "g_h", "source": str(UUID_G), "target": str(UUID_H)},
            {"id": "h_k", "source": str(UUID_H), "target": str(UUID_K)},
            {"id": "g_i", "source": str(UUID_G), "target": str(UUID_I)},
            {"id": "i_j", "source": str(UUID_I), "target": str(UUID_J)},
            {"id": "j_l", "source": str(UUID_J), "target": str(UUID_L)},
            {"id": "k_m", "source": str(UUID_K), "target": str(UUID_M)},
            {"id": "l_m", "source": str(UUID_L), "target": str(UUID_M)},
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
                "id": str(UUID_A),
                "type": "udf",
                "data": {"type": "udf"},
            },
            {
                "id": str(UUID_B),
                "type": "udf",
                "data": {"type": "udf"},
            },
            {
                "id": str(UUID_C),
                "type": "udf",
                "data": {"type": "udf"},
            },
            {
                "id": str(UUID_D),
                "type": "udf",
                "data": {"type": "udf"},
            },
            {
                "id": str(UUID_E),
                "type": "udf",
                "data": {"type": "udf"},
            },
            {
                "id": str(UUID_F),
                "type": "udf",
                "data": {"type": "udf"},
            },
            {
                "id": str(UUID_G),
                "type": "udf",
                "data": {"type": "udf"},
            },
        ],
        "edges": [
            {
                "id": "trigger_edge",
                "source": metadata.trigger.id,
                "target": str(UUID_A),
            },
            {"id": "a_b", "source": str(UUID_A), "target": str(UUID_B)},
            {"id": "a_c", "source": str(UUID_A), "target": str(UUID_C)},
            {"id": "b_d", "source": str(UUID_B), "target": str(UUID_D)},
            {"id": "b_e", "source": str(UUID_B), "target": str(UUID_E)},
            {"id": "c_f", "source": str(UUID_C), "target": str(UUID_F)},
            {"id": "c_g", "source": str(UUID_C), "target": str(UUID_G)},
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
                "id": str(UUID_A),
                "type": "udf",
                "data": {"type": "udf"},
            },
            {
                "id": str(UUID_B),
                "type": "udf",
                "data": {"type": "udf"},
            },
            {
                "id": str(UUID_C),
                "type": "udf",
                "data": {"type": "udf"},
            },
            {
                "id": str(UUID_D),
                "type": "udf",
                "data": {"type": "udf"},
            },
            {
                "id": str(UUID_E),
                "type": "udf",
                "data": {"type": "udf"},
            },
            {
                "id": str(UUID_F),
                "type": "udf",
                "data": {"type": "udf"},
            },
            {
                "id": str(UUID_G),
                "type": "udf",
                "data": {"type": "udf"},
            },
        ],
        "edges": [
            {
                "id": "trigger_edge",
                "source": metadata.trigger.id,
                "target": str(UUID_A),
            },
            {"id": "a_b", "source": str(UUID_A), "target": str(UUID_B)},
            {"id": "a_e", "source": str(UUID_A), "target": str(UUID_E)},
            {"id": "b_c", "source": str(UUID_B), "target": str(UUID_C)},
            {"id": "b_d", "source": str(UUID_B), "target": str(UUID_D)},
            {"id": "e_f", "source": str(UUID_E), "target": str(UUID_F)},
            {"id": "f_g", "source": str(UUID_F), "target": str(UUID_G)},
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
                "id": str(UUID_A),
                "type": "udf",
                "data": {"type": "udf"},
            },
            {
                "id": str(UUID_B),
                "type": "udf",
                "data": {"type": "udf"},
            },
            {
                "id": str(UUID_C),
                "type": "udf",
                "data": {"type": "udf"},
            },
            {
                "id": str(UUID_D),
                "type": "udf",
                "data": {"type": "udf"},
            },
            {
                "id": str(UUID_E),
                "type": "udf",
                "data": {"type": "udf"},
            },
            {
                "id": str(UUID_F),
                "type": "udf",
                "data": {"type": "udf"},
            },
            {
                "id": str(UUID_G),
                "type": "udf",
                "data": {"type": "udf"},
            },
        ],
        "edges": [
            {
                "id": "trigger_edge",
                "source": metadata.trigger.id,
                "target": str(UUID_A),
            },
            {"id": "a_b", "source": str(UUID_A), "target": str(UUID_B)},
            {"id": "a_c", "source": str(UUID_A), "target": str(UUID_C)},
            {"id": "b_d", "source": str(UUID_B), "target": str(UUID_D)},
            {"id": "b_e", "source": str(UUID_B), "target": str(UUID_E)},
            {"id": "c_e", "source": str(UUID_C), "target": str(UUID_E)},
            {"id": "c_f", "source": str(UUID_C), "target": str(UUID_F)},
            {"id": "d_g", "source": str(UUID_D), "target": str(UUID_G)},
            {"id": "e_g", "source": str(UUID_E), "target": str(UUID_G)},
            {"id": "f_g", "source": str(UUID_F), "target": str(UUID_G)},
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
                "id": str(UUID_A),
                "type": "udf",
                "data": {"type": "udf"},
            },
            {
                "id": str(UUID_B),
                "type": "udf",
                "data": {"type": "udf"},
            },
            {
                "id": str(UUID_C),
                "type": "udf",
                "data": {"type": "udf"},
            },
            {
                "id": str(UUID_D),
                "type": "udf",
                "data": {"type": "udf"},
            },
            {
                "id": str(UUID_E),
                "type": "udf",
                "data": {"type": "udf"},
            },
            {
                "id": str(UUID_F),
                "type": "udf",
                "data": {"type": "udf"},
            },
            {
                "id": str(UUID_G),
                "type": "udf",
                "data": {"type": "udf"},
            },
            {
                "id": str(UUID_H),
                "type": "udf",
                "data": {"type": "udf"},
            },
            {
                "id": str(UUID_I),
                "type": "udf",
                "data": {"type": "udf"},
            },
        ],
        "edges": [
            {
                "id": "trigger_edge",
                "source": metadata.trigger.id,
                "target": str(UUID_A),
            },
            {"id": "a_b", "source": str(UUID_A), "target": str(UUID_B)},
            {"id": "a_c", "source": str(UUID_A), "target": str(UUID_C)},
            {"id": "b_d", "source": str(UUID_B), "target": str(UUID_D)},
            {"id": "b_e", "source": str(UUID_B), "target": str(UUID_E)},
            {"id": "c_e", "source": str(UUID_C), "target": str(UUID_E)},
            {"id": "c_f", "source": str(UUID_C), "target": str(UUID_F)},
            {"id": "d_g", "source": str(UUID_D), "target": str(UUID_G)},
            {"id": "e_g", "source": str(UUID_E), "target": str(UUID_G)},
            {"id": "e_h", "source": str(UUID_E), "target": str(UUID_H)},
            {"id": "f_h", "source": str(UUID_F), "target": str(UUID_H)},
            {"id": "g_i", "source": str(UUID_G), "target": str(UUID_I)},
            {"id": "h_i", "source": str(UUID_H), "target": str(UUID_I)},
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
                "id": str(UUID_A),
                "type": "udf",
                "data": {"type": "udf"},
            },
            {
                "id": str(UUID_B),
                "type": "udf",
                "data": {"type": "udf"},
            },
            {
                "id": str(UUID_C),
                "type": "udf",
                "data": {"type": "udf"},
            },
            {
                "id": str(UUID_D),
                "type": "udf",
                "data": {"type": "udf"},
            },
            {
                "id": str(UUID_E),
                "type": "udf",
                "data": {"type": "udf"},
            },
            {
                "id": str(UUID_F),
                "type": "udf",
                "data": {"type": "udf"},
            },
            {
                "id": str(UUID_G),
                "type": "udf",
                "data": {"type": "udf"},
            },
            {
                "id": str(UUID_H),
                "type": "udf",
                "data": {"type": "udf"},
            },
            {
                "id": str(UUID_I),
                "type": "udf",
                "data": {"type": "udf"},
            },
            {
                "id": str(UUID_J),
                "type": "udf",
                "data": {"type": "udf"},
            },
            {
                "id": str(UUID_K),
                "type": "udf",
                "data": {"type": "udf"},
            },
            {
                "id": str(UUID_L),
                "type": "udf",
                "data": {"type": "udf"},
            },
        ],
        "edges": [
            {
                "id": "trigger_edge",
                "source": metadata.trigger.id,
                "target": str(UUID_A),
            },
            {"id": "a_b", "source": str(UUID_A), "target": str(UUID_B)},
            {"id": "a_e", "source": str(UUID_A), "target": str(UUID_E)},
            {"id": "b_c", "source": str(UUID_B), "target": str(UUID_C)},
            {"id": "b_d", "source": str(UUID_B), "target": str(UUID_D)},
            {"id": "c_g", "source": str(UUID_C), "target": str(UUID_G)},
            {"id": "d_g", "source": str(UUID_D), "target": str(UUID_G)},
            {"id": "e_f", "source": str(UUID_E), "target": str(UUID_F)},
            {"id": "e_i", "source": str(UUID_E), "target": str(UUID_I)},
            {"id": "e_h", "source": str(UUID_E), "target": str(UUID_H)},
            {"id": "f_j", "source": str(UUID_F), "target": str(UUID_J)},
            {"id": "g_k", "source": str(UUID_G), "target": str(UUID_K)},
            {"id": "h_k", "source": str(UUID_H), "target": str(UUID_K)},
            {"id": "i_j", "source": str(UUID_I), "target": str(UUID_J)},
            {"id": "j_l", "source": str(UUID_J), "target": str(UUID_L)},
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
