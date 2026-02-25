import uuid
from typing import TYPE_CHECKING, cast

import pytest
from pydantic import BaseModel

from tracecat.dsl.common import (
    DSLEntrypoint,
    DSLInput,
    UpstreamEdgeData,
    build_action_statements_from_actions,
)
from tracecat.dsl.enums import EdgeType
from tracecat.dsl.schemas import ActionStatement
from tracecat.dsl.view import RFEdge, RFGraph, TriggerNode, TriggerNodeData, UDFNode
from tracecat.exceptions import TracecatValidationError

if TYPE_CHECKING:
    from tracecat.db.models import Action, Workflow

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


def test_single_action_node(metadata):
    """Single action node - simplest non-trivial case.

    trigger -> A

    Checks:
    1. A single action node with no dependencies works correctly
    2. The graph has exactly one entrypoint
    3. No action-to-action edges exist
    """
    rf_obj = {
        "nodes": [
            metadata.trigger,
            {
                "id": str(UUID_A),
                "type": "udf",
                "data": {"type": "udf", "args": {"test": 1}},
            },
        ],
        "edges": [
            {
                "id": "trigger_edge",
                "source": metadata.trigger.id,
                "target": str(UUID_A),
            },
        ],
    }

    expected_wf_ir = [
        ActionStatement(
            ref="a",
            action="udf",
            args={"test": 1},
        ),
    ]
    graph = RFGraph.model_validate(rf_obj)

    # Verify graph structure
    assert len(graph.action_nodes()) == 1
    assert len(graph.action_edges()) == 0  # No action-to-action edges
    assert len(graph.entrypoints) == 1
    assert graph.entrypoints[0].id == UUID_A

    stmts = build_actions(graph)
    dsl = DSLInput(actions=stmts, **metadata.model_dump())
    assert dsl.actions == expected_wf_ir


def test_parallel_branches_no_convergence(metadata):
    r"""Parallel branches that never converge.

    trigger -> A -> B
    trigger -> C -> D

    Checks:
    1. Multiple entrypoints work correctly
    2. Independent parallel branches are parsed correctly
    3. No dependencies between the branches
    """
    rf_obj = {
        "nodes": [
            metadata.trigger,
            {"id": str(UUID_A), "type": "udf", "data": {"type": "udf"}},
            {"id": str(UUID_B), "type": "udf", "data": {"type": "udf"}},
            {"id": str(UUID_C), "type": "udf", "data": {"type": "udf"}},
            {"id": str(UUID_D), "type": "udf", "data": {"type": "udf"}},
        ],
        "edges": [
            {"id": "trigger_a", "source": metadata.trigger.id, "target": str(UUID_A)},
            {"id": "trigger_c", "source": metadata.trigger.id, "target": str(UUID_C)},
            {"id": "a_b", "source": str(UUID_A), "target": str(UUID_B)},
            {"id": "c_d", "source": str(UUID_C), "target": str(UUID_D)},
        ],
    }

    expected_wf_ir = [
        ActionStatement(ref="a", action="udf", args={}),
        ActionStatement(ref="b", action="udf", args={}, depends_on=["a"]),
        ActionStatement(ref="c", action="udf", args={}),
        ActionStatement(ref="d", action="udf", args={}, depends_on=["c"]),
    ]
    graph = RFGraph.model_validate(rf_obj)

    # Verify graph structure
    assert len(graph.action_nodes()) == 4
    assert len(graph.action_edges()) == 2  # A->B, C->D
    assert len(graph.entrypoints) == 2  # A and C
    entrypoint_ids = {ep.id for ep in graph.entrypoints}
    assert entrypoint_ids == {UUID_A, UUID_C}

    stmts = build_actions(graph)
    dsl = DSLInput(actions=stmts, **metadata.model_dump())
    assert dsl.actions == expected_wf_ir


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


# =============================================================================
# Tests for RFGraph.from_actions
# =============================================================================


class MockAction:
    """Mock Action object for testing RFGraph.from_actions.

    Simulates the Action DB model with required attributes.
    """

    def __init__(
        self,
        id: uuid.UUID,
        type: str,
        title: str,
        position_x: float = 0.0,
        position_y: float = 0.0,
        upstream_edges: list[UpstreamEdgeData] | None = None,
    ):
        self.id = id
        self.type = type
        self.title = title
        self.position_x = position_x
        self.position_y = position_y
        self.upstream_edges = upstream_edges if upstream_edges is not None else []


class MockWorkflow:
    """Mock Workflow object for testing RFGraph.from_actions.

    Simulates the Workflow DB model with required attributes.
    """

    def __init__(
        self,
        id: uuid.UUID,
        status: str = "offline",
        trigger_position_x: float = 0.0,
        trigger_position_y: float = 0.0,
    ):
        self.id = id
        self.status = status
        self.trigger_position_x = trigger_position_x
        self.trigger_position_y = trigger_position_y


def test_from_actions_simple_sequence():
    """Test RFGraph.from_actions with a simple sequence: trigger -> A -> B -> C.

    This tests that upstream_edges properly create dependencies.
    """
    workflow = MockWorkflow(id=WORKFLOW_UUID)
    trigger_id = f"trigger-{WORKFLOW_UUID}"

    # A is entrypoint (connected to trigger), B depends on A, C depends on B
    action_a = MockAction(
        id=UUID_A,
        type="udf",
        title="Action A",
        upstream_edges=[
            {"source_id": trigger_id, "source_type": "trigger"},
        ],
    )
    action_b = MockAction(
        id=UUID_B,
        type="udf",
        title="Action B",
        upstream_edges=[
            {
                "source_id": str(UUID_A),
                "source_type": "udf",
                "source_handle": "success",
            },
        ],
    )
    action_c = MockAction(
        id=UUID_C,
        type="udf",
        title="Action C",
        upstream_edges=[
            {
                "source_id": str(UUID_B),
                "source_type": "udf",
                "source_handle": "success",
            },
        ],
    )

    graph = RFGraph.from_actions(
        cast("Workflow", workflow),
        cast("list[Action]", [action_a, action_b, action_c]),
    )

    # Verify nodes structure and count
    assert len(graph.nodes) == 4  # 1 trigger + 3 actions
    # Verify all nodes are the expected types
    assert all(isinstance(node, TriggerNode | UDFNode) for node in graph.nodes)
    # Verify trigger node exists and is correct type
    trigger_nodes = [node for node in graph.nodes if isinstance(node, TriggerNode)]
    assert len(trigger_nodes) == 1
    assert isinstance(trigger_nodes[0], TriggerNode)

    action_nodes = graph.action_nodes()
    assert len(action_nodes) == 3
    # Verify all action nodes are UDFNode instances
    assert all(isinstance(node, UDFNode) for node in action_nodes)
    assert {node.id for node in action_nodes} == {UUID_A, UUID_B, UUID_C}

    # Verify edges structure and count (including trigger edge)
    assert len(graph.edges) == 3  # trigger->A, A->B, B->C
    # Verify all edges are RFEdge instances
    assert all(isinstance(edge, RFEdge) for edge in graph.edges)

    # Verify exact edge connections: trigger->A, A->B, B->C
    edge_sources_targets = {
        (str(edge.source), str(edge.target)) for edge in graph.edges
    }
    expected_edges = {
        (trigger_id, str(UUID_A)),  # trigger->A
        (str(UUID_A), str(UUID_B)),  # A->B
        (str(UUID_B), str(UUID_C)),  # B->C
    }
    assert edge_sources_targets == expected_edges, (
        f"Expected edges {expected_edges}, but got {edge_sources_targets}"
    )

    # Verify action edges (excluding trigger)
    action_edges = graph.action_edges()
    assert len(action_edges) == 2  # A->B, B->C
    # Verify all action edges are RFEdge instances
    assert all(isinstance(edge, RFEdge) for edge in action_edges)
    # Verify exact action edge connections: A->B, B->C
    action_edge_sources_targets = {
        (str(edge.source), str(edge.target)) for edge in action_edges
    }
    expected_action_edges = {
        (str(UUID_A), str(UUID_B)),  # A->B
        (str(UUID_B), str(UUID_C)),  # B->C
    }
    assert action_edge_sources_targets == expected_action_edges, (
        f"Expected action edges {expected_action_edges}, "
        f"but got {action_edge_sources_targets}"
    )

    # Verify dependency list (for action-to-action edges only)
    dep_list = graph.dep_list
    assert dep_list[UUID_A] == set()  # A is entrypoint
    assert dep_list[UUID_B] == {UUID_A}  # B depends on A
    assert dep_list[UUID_C] == {UUID_B}  # C depends on B


def test_from_actions_diamond():
    r"""Test RFGraph.from_actions with a diamond pattern:

       trigger -> A
                  /\
                 B  C
                  \/
                   D
    """
    workflow = MockWorkflow(id=WORKFLOW_UUID)
    trigger_id = f"trigger-{WORKFLOW_UUID}"

    action_a = MockAction(
        id=UUID_A,
        type="udf",
        title="Action A",
        upstream_edges=[
            {"source_id": trigger_id, "source_type": "trigger"},
        ],
    )
    action_b = MockAction(
        id=UUID_B,
        type="udf",
        title="Action B",
        upstream_edges=[
            {
                "source_id": str(UUID_A),
                "source_type": "udf",
                "source_handle": "success",
            },
        ],
    )
    action_c = MockAction(
        id=UUID_C,
        type="udf",
        title="Action C",
        upstream_edges=[
            {
                "source_id": str(UUID_A),
                "source_type": "udf",
                "source_handle": "success",
            },
        ],
    )
    action_d = MockAction(
        id=UUID_D,
        type="udf",
        title="Action D",
        upstream_edges=[
            {
                "source_id": str(UUID_B),
                "source_type": "udf",
                "source_handle": "success",
            },
            {
                "source_id": str(UUID_C),
                "source_type": "udf",
                "source_handle": "success",
            },
        ],
    )

    graph = RFGraph.from_actions(
        cast("Workflow", workflow),
        cast("list[Action]", [action_a, action_b, action_c, action_d]),
    )

    # Verify nodes structure and count
    assert len(graph.nodes) == 5  # 1 trigger + 4 actions
    # Verify all nodes are the expected types
    assert all(isinstance(node, TriggerNode | UDFNode) for node in graph.nodes)
    # Verify trigger node exists and is correct type
    trigger_nodes = [node for node in graph.nodes if isinstance(node, TriggerNode)]
    assert len(trigger_nodes) == 1
    assert isinstance(trigger_nodes[0], TriggerNode)

    action_nodes = graph.action_nodes()
    assert len(action_nodes) == 4
    # Verify all action nodes are UDFNode instances
    assert all(isinstance(node, UDFNode) for node in action_nodes)

    # Verify all edges structure and count
    assert len(graph.edges) == 5  # trigger->A, A->B, A->C, B->D, C->D
    # Verify all edges are RFEdge instances
    assert all(isinstance(edge, RFEdge) for edge in graph.edges)

    # Verify exact edge connections: trigger->A, A->B, A->C, B->D, C->D
    edge_sources_targets = {
        (str(edge.source), str(edge.target)) for edge in graph.edges
    }
    expected_edges = {
        (trigger_id, str(UUID_A)),  # trigger->A
        (str(UUID_A), str(UUID_B)),  # A->B
        (str(UUID_A), str(UUID_C)),  # A->C
        (str(UUID_B), str(UUID_D)),  # B->D
        (str(UUID_C), str(UUID_D)),  # C->D
    }
    assert edge_sources_targets == expected_edges, (
        f"Expected edges {expected_edges}, but got {edge_sources_targets}"
    )

    # Verify action edges structure and count
    action_edges = graph.action_edges()
    assert len(action_edges) == 4  # A->B, A->C, B->D, C->D
    # Verify all action edges are RFEdge instances
    assert all(isinstance(edge, RFEdge) for edge in action_edges)
    # Verify exact action edge connections: A->B, A->C, B->D, C->D
    action_edge_sources_targets = {
        (str(edge.source), str(edge.target)) for edge in action_edges
    }
    expected_action_edges = {
        (str(UUID_A), str(UUID_B)),  # A->B
        (str(UUID_A), str(UUID_C)),  # A->C
        (str(UUID_B), str(UUID_D)),  # B->D
        (str(UUID_C), str(UUID_D)),  # C->D
    }
    assert action_edge_sources_targets == expected_action_edges, (
        f"Expected action edges {expected_action_edges}, "
        f"but got {action_edge_sources_targets}"
    )

    # Verify dependency list
    dep_list = graph.dep_list
    assert dep_list[UUID_A] == set()  # A is entrypoint
    assert dep_list[UUID_B] == {UUID_A}  # B depends on A
    assert dep_list[UUID_C] == {UUID_A}  # C depends on A
    assert dep_list[UUID_D] == {UUID_B, UUID_C}  # D depends on B and C


def test_from_actions_multiple_entrypoints():
    """Test RFGraph.from_actions with multiple entrypoints:

    trigger -> A
    trigger -> B
               A -> C
               B -> C
    """
    workflow = MockWorkflow(id=WORKFLOW_UUID)
    trigger_id = f"trigger-{WORKFLOW_UUID}"

    action_a = MockAction(
        id=UUID_A,
        type="udf",
        title="Action A",
        upstream_edges=[
            {"source_id": trigger_id, "source_type": "trigger"},
        ],
    )
    action_b = MockAction(
        id=UUID_B,
        type="udf",
        title="Action B",
        upstream_edges=[
            {"source_id": trigger_id, "source_type": "trigger"},
        ],
    )
    action_c = MockAction(
        id=UUID_C,
        type="udf",
        title="Action C",
        upstream_edges=[
            {
                "source_id": str(UUID_A),
                "source_type": "udf",
                "source_handle": "success",
            },
            {
                "source_id": str(UUID_B),
                "source_type": "udf",
                "source_handle": "success",
            },
        ],
    )

    graph = RFGraph.from_actions(
        cast("Workflow", workflow),
        cast("list[Action]", [action_a, action_b, action_c]),
    )

    # Verify nodes structure and count
    assert len(graph.nodes) == 4  # 1 trigger + 3 actions
    # Verify all nodes are the expected types
    assert all(isinstance(node, TriggerNode | UDFNode) for node in graph.nodes)
    # Verify trigger node exists and is correct type
    trigger_nodes = [node for node in graph.nodes if isinstance(node, TriggerNode)]
    assert len(trigger_nodes) == 1
    assert isinstance(trigger_nodes[0], TriggerNode)

    # Verify all edges structure and count
    assert len(graph.edges) == 4  # trigger->A, trigger->B, A->C, B->C
    # Verify all edges are RFEdge instances
    assert all(isinstance(edge, RFEdge) for edge in graph.edges)

    # Verify exact edge connections: trigger->A, trigger->B, A->C, B->C
    edge_sources_targets = {
        (str(edge.source), str(edge.target)) for edge in graph.edges
    }
    expected_edges = {
        (trigger_id, str(UUID_A)),  # trigger->A
        (trigger_id, str(UUID_B)),  # trigger->B
        (str(UUID_A), str(UUID_C)),  # A->C
        (str(UUID_B), str(UUID_C)),  # B->C
    }
    assert edge_sources_targets == expected_edges, (
        f"Expected edges {expected_edges}, but got {edge_sources_targets}"
    )

    # Verify action edges (excludes trigger edges)
    action_edges = graph.action_edges()
    assert len(action_edges) == 2  # A->C, B->C
    # Verify all action edges are RFEdge instances
    assert all(isinstance(edge, RFEdge) for edge in action_edges)
    # Verify exact action edge connections: A->C, B->C
    action_edge_sources_targets = {
        (str(edge.source), str(edge.target)) for edge in action_edges
    }
    expected_action_edges = {
        (str(UUID_A), str(UUID_C)),  # A->C
        (str(UUID_B), str(UUID_C)),  # B->C
    }
    assert action_edge_sources_targets == expected_action_edges, (
        f"Expected action edges {expected_action_edges}, "
        f"but got {action_edge_sources_targets}"
    )

    # Verify entrypoints (nodes with indegree 0 in action graph)
    entrypoints = graph.entrypoints
    assert len(entrypoints) == 2
    assert {ep.id for ep in entrypoints} == {UUID_A, UUID_B}

    # Verify dependency list
    dep_list = graph.dep_list
    assert dep_list[UUID_A] == set()  # A is entrypoint
    assert dep_list[UUID_B] == set()  # B is entrypoint
    assert dep_list[UUID_C] == {UUID_A, UUID_B}  # C depends on A and B


def test_from_actions_error_edge():
    """Test RFGraph.from_actions with error edges:

    trigger -> A
               A.success -> B
               A.error -> C
    """
    workflow = MockWorkflow(id=WORKFLOW_UUID)
    trigger_id = f"trigger-{WORKFLOW_UUID}"

    action_a = MockAction(
        id=UUID_A,
        type="udf",
        title="Action A",
        upstream_edges=[
            {"source_id": trigger_id, "source_type": "trigger"},
        ],
    )
    action_b = MockAction(
        id=UUID_B,
        type="udf",
        title="Action B",
        upstream_edges=[
            {
                "source_id": str(UUID_A),
                "source_type": "udf",
                "source_handle": "success",
            },
        ],
    )
    action_c = MockAction(
        id=UUID_C,
        type="udf",
        title="Action C",
        upstream_edges=[
            {"source_id": str(UUID_A), "source_type": "udf", "source_handle": "error"},
        ],
    )

    graph = RFGraph.from_actions(
        cast("Workflow", workflow),
        cast("list[Action]", [action_a, action_b, action_c]),
    )

    # Verify nodes structure and count
    assert len(graph.nodes) == 4  # 1 trigger + 3 actions
    # Verify all nodes are the expected types
    assert all(isinstance(node, TriggerNode | UDFNode) for node in graph.nodes)
    # Verify trigger node exists and is correct type
    trigger_nodes = [node for node in graph.nodes if isinstance(node, TriggerNode)]
    assert len(trigger_nodes) == 1
    assert isinstance(trigger_nodes[0], TriggerNode)

    # Verify all edges structure and count
    assert len(graph.edges) == 3  # trigger->A, A->B (success), A->C (error)
    # Verify all edges are RFEdge instances
    assert all(isinstance(edge, RFEdge) for edge in graph.edges)

    # Verify exact edge connections: trigger->A, A->B, A->C
    edge_sources_targets = {
        (str(edge.source), str(edge.target)) for edge in graph.edges
    }
    expected_edges = {
        (trigger_id, str(UUID_A)),  # trigger->A
        (str(UUID_A), str(UUID_B)),  # A->B
        (str(UUID_A), str(UUID_C)),  # A->C
    }
    assert edge_sources_targets == expected_edges, (
        f"Expected edges {expected_edges}, but got {edge_sources_targets}"
    )

    # Verify action edges structure and count
    action_edges = graph.action_edges()
    assert len(action_edges) == 2  # A->B (success), A->C (error)
    # Verify all action edges are RFEdge instances
    assert all(isinstance(edge, RFEdge) for edge in action_edges)
    # Verify exact action edge connections: A->B, A->C
    action_edge_sources_targets = {
        (str(edge.source), str(edge.target)) for edge in action_edges
    }
    expected_action_edges = {
        (str(UUID_A), str(UUID_B)),  # A->B
        (str(UUID_A), str(UUID_C)),  # A->C
    }
    assert action_edge_sources_targets == expected_action_edges, (
        f"Expected action edges {expected_action_edges}, "
        f"but got {action_edge_sources_targets}"
    )

    # Find the edges and verify their source handles
    edge_to_b = next(e for e in action_edges if str(e.target) == str(UUID_B))
    edge_to_c = next(e for e in action_edges if str(e.target) == str(UUID_C))

    assert edge_to_b.source_handle == EdgeType.SUCCESS
    assert edge_to_c.source_handle == EdgeType.ERROR


def test_from_actions_empty_graph():
    """Test RFGraph.from_actions with no actions (only trigger)."""
    workflow = MockWorkflow(id=WORKFLOW_UUID)

    graph = RFGraph.from_actions(cast("Workflow", workflow), [])

    # Only trigger node
    assert len(graph.nodes) == 1
    assert graph.nodes[0].type == "trigger"
    assert len(graph.edges) == 0


def test_from_actions_adjacency_list():
    """Test that adjacency list is correctly built from from_actions.

    trigger -> A -> B -> C
    """
    workflow = MockWorkflow(id=WORKFLOW_UUID)
    trigger_id = f"trigger-{WORKFLOW_UUID}"

    action_a = MockAction(
        id=UUID_A,
        type="udf",
        title="Action A",
        upstream_edges=[
            {"source_id": trigger_id, "source_type": "trigger"},
        ],
    )
    action_b = MockAction(
        id=UUID_B,
        type="udf",
        title="Action B",
        upstream_edges=[
            {
                "source_id": str(UUID_A),
                "source_type": "udf",
                "source_handle": "success",
            },
        ],
    )
    action_c = MockAction(
        id=UUID_C,
        type="udf",
        title="Action C",
        upstream_edges=[
            {
                "source_id": str(UUID_B),
                "source_type": "udf",
                "source_handle": "success",
            },
        ],
    )

    graph = RFGraph.from_actions(
        cast("Workflow", workflow),
        cast("list[Action]", [action_a, action_b, action_c]),
    )

    # Verify adjacency list
    adj_list = graph.adj_list
    assert UUID_A in adj_list
    assert UUID_B in adj_list
    assert UUID_C in adj_list

    # A points to B
    assert UUID_B in adj_list[UUID_A]
    # B points to C
    assert UUID_C in adj_list[UUID_B]
    # C points to nothing
    assert adj_list[UUID_C] == []


def test_from_actions_positions():
    """Test that node positions are correctly preserved from from_actions."""
    workflow = MockWorkflow(
        id=WORKFLOW_UUID,
        trigger_position_x=100.0,
        trigger_position_y=200.0,
    )
    trigger_id = f"trigger-{WORKFLOW_UUID}"

    action_a = MockAction(
        id=UUID_A,
        type="udf",
        title="Action A",
        position_x=300.0,
        position_y=400.0,
        upstream_edges=[
            {"source_id": trigger_id, "source_type": "trigger"},
        ],
    )

    graph = RFGraph.from_actions(
        cast("Workflow", workflow),
        cast("list[Action]", [action_a]),
    )

    # Verify trigger position
    assert graph.trigger.position.x == 100.0
    assert graph.trigger.position.y == 200.0

    # Verify action position
    action_node = graph.action_nodes()[0]
    assert action_node.position.x == 300.0
    assert action_node.position.y == 400.0


def test_from_actions_roundtrip_to_action_statements():
    """Test full round-trip: upstream_edges -> RFGraph -> depends_on.

    This tests that upstream_edges correctly flow through to ActionStatement depends_on
    when building statements from the graph.

       trigger -> A -> B -> C
    """
    workflow = MockWorkflow(id=WORKFLOW_UUID)
    trigger_id = f"trigger-{WORKFLOW_UUID}"

    action_a = MockAction(
        id=UUID_A,
        type="udf",
        title="Action A",
        upstream_edges=[
            {"source_id": trigger_id, "source_type": "trigger"},
        ],
    )
    action_b = MockAction(
        id=UUID_B,
        type="udf",
        title="Action B",
        upstream_edges=[
            {
                "source_id": str(UUID_A),
                "source_type": "udf",
                "source_handle": "success",
            },
        ],
    )
    action_c = MockAction(
        id=UUID_C,
        type="udf",
        title="Action C",
        upstream_edges=[
            {
                "source_id": str(UUID_B),
                "source_type": "udf",
                "source_handle": "success",
            },
        ],
    )

    graph = RFGraph.from_actions(
        cast("Workflow", workflow),
        cast("list[Action]", [action_a, action_b, action_c]),
    )

    # Build action statements using the same helper as other tests
    stmts = build_actions(graph)

    # Sort by ref for consistent comparison
    stmts_by_ref = {stmt.ref: stmt for stmt in stmts}

    # Verify depends_on is correctly constructed
    assert stmts_by_ref["a"].depends_on == []  # A is entrypoint
    assert stmts_by_ref["b"].depends_on == ["a"]  # B depends on A
    assert stmts_by_ref["c"].depends_on == ["b"]  # C depends on B


def test_from_actions_roundtrip_diamond():
    r"""Test full round-trip for diamond pattern: upstream_edges -> RFGraph -> depends_on.

       trigger -> A
                  /\\
                 B  C
                  \\/
                   D
    """
    workflow = MockWorkflow(id=WORKFLOW_UUID)
    trigger_id = f"trigger-{WORKFLOW_UUID}"

    action_a = MockAction(
        id=UUID_A,
        type="udf",
        title="Action A",
        upstream_edges=[
            {"source_id": trigger_id, "source_type": "trigger"},
        ],
    )
    action_b = MockAction(
        id=UUID_B,
        type="udf",
        title="Action B",
        upstream_edges=[
            {
                "source_id": str(UUID_A),
                "source_type": "udf",
                "source_handle": "success",
            },
        ],
    )
    action_c = MockAction(
        id=UUID_C,
        type="udf",
        title="Action C",
        upstream_edges=[
            {
                "source_id": str(UUID_A),
                "source_type": "udf",
                "source_handle": "success",
            },
        ],
    )
    action_d = MockAction(
        id=UUID_D,
        type="udf",
        title="Action D",
        upstream_edges=[
            {
                "source_id": str(UUID_B),
                "source_type": "udf",
                "source_handle": "success",
            },
            {
                "source_id": str(UUID_C),
                "source_type": "udf",
                "source_handle": "success",
            },
        ],
    )

    graph = RFGraph.from_actions(
        cast("Workflow", workflow),
        cast("list[Action]", [action_a, action_b, action_c, action_d]),
    )

    # Build action statements using the same helper as other tests
    stmts = build_actions(graph)

    # Sort by ref for consistent comparison
    stmts_by_ref = {stmt.ref: stmt for stmt in stmts}

    # Verify depends_on is correctly constructed
    assert stmts_by_ref["a"].depends_on == []  # A is entrypoint
    assert stmts_by_ref["b"].depends_on == ["a"]  # B depends on A
    assert stmts_by_ref["c"].depends_on == ["a"]  # C depends on A
    assert sorted(stmts_by_ref["d"].depends_on) == ["b", "c"]  # D depends on B and C


# =============================================================================
# Tests for build_action_statements_from_actions
# =============================================================================


class MockActionWithRef:
    """Mock Action with ref property for testing build_action_statements_from_actions."""

    def __init__(
        self,
        id: uuid.UUID,
        type: str,
        title: str,
        ref: str,
        inputs: str = "",
        control_flow: dict | None = None,
        is_interactive: bool = False,
        interaction: dict | None = None,
        upstream_edges: list[dict] | None = None,
        position_x: float = 0.0,
        position_y: float = 0.0,
    ):
        self.id = id
        self.type = type
        self.title = title
        self._ref = ref
        self.inputs = inputs
        self.control_flow = control_flow or {}
        self.is_interactive = is_interactive
        self.interaction = interaction
        self.upstream_edges = upstream_edges if upstream_edges is not None else []
        self.position_x = position_x
        self.position_y = position_y

    @property
    def ref(self) -> str:
        return self._ref


def test_build_action_statements_simple_sequence():
    """Test build_action_statements_from_actions with a simple sequence.

    trigger -> A -> B -> C

    Verifies that depends_on is correctly built from upstream_edges.
    """
    trigger_id = f"trigger-{WORKFLOW_UUID}"

    action_a = MockActionWithRef(
        id=UUID_A,
        type="udf",
        title="Action A",
        ref="action_a",
        upstream_edges=[
            {"source_id": trigger_id, "source_type": "trigger"},
        ],
    )
    action_b = MockActionWithRef(
        id=UUID_B,
        type="udf",
        title="Action B",
        ref="action_b",
        upstream_edges=[
            {
                "source_id": str(UUID_A),
                "source_type": "udf",
                "source_handle": "success",
            },
        ],
    )
    action_c = MockActionWithRef(
        id=UUID_C,
        type="udf",
        title="Action C",
        ref="action_c",
        upstream_edges=[
            {
                "source_id": str(UUID_B),
                "source_type": "udf",
                "source_handle": "success",
            },
        ],
    )

    stmts = build_action_statements_from_actions(
        cast("list[Action]", [action_a, action_b, action_c])
    )
    stmts_by_ref = {stmt.ref: stmt for stmt in stmts}

    # Verify depends_on is correctly constructed
    # Trigger edges should be skipped (not valid UUID)
    assert stmts_by_ref["action_a"].depends_on == []  # A is entrypoint
    assert stmts_by_ref["action_b"].depends_on == ["action_a"]  # B depends on A
    assert stmts_by_ref["action_c"].depends_on == ["action_b"]  # C depends on B


def test_build_action_statements_diamond():
    r"""Test build_action_statements_from_actions with diamond pattern.

       trigger -> A
                  /\\
                 B  C
                  \\/
                   D
    """
    trigger_id = f"trigger-{WORKFLOW_UUID}"

    action_a = MockActionWithRef(
        id=UUID_A,
        type="udf",
        title="Action A",
        ref="action_a",
        upstream_edges=[
            {"source_id": trigger_id, "source_type": "trigger"},
        ],
    )
    action_b = MockActionWithRef(
        id=UUID_B,
        type="udf",
        title="Action B",
        ref="action_b",
        upstream_edges=[
            {
                "source_id": str(UUID_A),
                "source_type": "udf",
                "source_handle": "success",
            },
        ],
    )
    action_c = MockActionWithRef(
        id=UUID_C,
        type="udf",
        title="Action C",
        ref="action_c",
        upstream_edges=[
            {
                "source_id": str(UUID_A),
                "source_type": "udf",
                "source_handle": "success",
            },
        ],
    )
    action_d = MockActionWithRef(
        id=UUID_D,
        type="udf",
        title="Action D",
        ref="action_d",
        upstream_edges=[
            {
                "source_id": str(UUID_B),
                "source_type": "udf",
                "source_handle": "success",
            },
            {
                "source_id": str(UUID_C),
                "source_type": "udf",
                "source_handle": "success",
            },
        ],
    )

    stmts = build_action_statements_from_actions(
        cast("list[Action]", [action_a, action_b, action_c, action_d])
    )
    stmts_by_ref = {stmt.ref: stmt for stmt in stmts}

    # Verify depends_on is correctly constructed
    assert stmts_by_ref["action_a"].depends_on == []  # A is entrypoint
    assert stmts_by_ref["action_b"].depends_on == ["action_a"]  # B depends on A
    assert stmts_by_ref["action_c"].depends_on == ["action_a"]  # C depends on A
    # D depends on both B and C (sorted)
    assert sorted(stmts_by_ref["action_d"].depends_on) == ["action_b", "action_c"]


def test_build_action_statements_error_edges():
    """Test build_action_statements_from_actions with error edges.

    trigger -> A
               A.success -> B
               A.error -> C
    """
    trigger_id = f"trigger-{WORKFLOW_UUID}"

    action_a = MockActionWithRef(
        id=UUID_A,
        type="udf",
        title="Action A",
        ref="action_a",
        upstream_edges=[
            {"source_id": trigger_id, "source_type": "trigger"},
        ],
    )
    action_b = MockActionWithRef(
        id=UUID_B,
        type="udf",
        title="Action B",
        ref="action_b",
        upstream_edges=[
            {
                "source_id": str(UUID_A),
                "source_type": "udf",
                "source_handle": "success",
            },
        ],
    )
    action_c = MockActionWithRef(
        id=UUID_C,
        type="udf",
        title="Action C",
        ref="action_c",
        upstream_edges=[
            {"source_id": str(UUID_A), "source_type": "udf", "source_handle": "error"},
        ],
    )

    stmts = build_action_statements_from_actions(
        cast("list[Action]", [action_a, action_b, action_c])
    )
    stmts_by_ref = {stmt.ref: stmt for stmt in stmts}

    # Verify depends_on includes error suffix for error edges
    assert stmts_by_ref["action_a"].depends_on == []
    assert stmts_by_ref["action_b"].depends_on == ["action_a"]  # success edge
    assert stmts_by_ref["action_c"].depends_on == ["action_a.error"]  # error edge


# =============================================================================
# Tests for build_action_statements (verifies the bug fix)
# =============================================================================


def test_build_action_statements_from_actions_fixes_depends_on_bug():
    """Test that build_action_statements_from_actions correctly builds depends_on.

    This tests the fix for the bug where build_action_statements() with
    RFGraph.from_actions() produced empty depends_on lists due to type mismatch
    (string source_id vs UUID dep_act_id comparison).

    The fix uses build_action_statements_from_actions() which reads upstream_edges
    directly from actions without going through the graph.

    trigger -> A -> B -> C
    """
    trigger_id = f"trigger-{WORKFLOW_UUID}"

    action_a = MockActionWithRef(
        id=UUID_A,
        type="udf",
        title="Action A",
        ref="action_a",
        upstream_edges=[
            {"source_id": trigger_id, "source_type": "trigger"},
        ],
    )
    action_b = MockActionWithRef(
        id=UUID_B,
        type="udf",
        title="Action B",
        ref="action_b",
        upstream_edges=[
            {
                "source_id": str(UUID_A),
                "source_type": "udf",
                "source_handle": "success",
            },
        ],
    )
    action_c = MockActionWithRef(
        id=UUID_C,
        type="udf",
        title="Action C",
        ref="action_c",
        upstream_edges=[
            {
                "source_id": str(UUID_B),
                "source_type": "udf",
                "source_handle": "success",
            },
        ],
    )

    actions = [action_a, action_b, action_c]

    # Build action statements using the fixed function (what workflow save now uses)
    stmts = build_action_statements_from_actions(cast("list[Action]", actions))
    stmts_by_ref = {stmt.ref: stmt for stmt in stmts}

    # Verify depends_on is correctly constructed
    assert (
        stmts_by_ref["action_a"].depends_on == []
    )  # A is entrypoint (trigger edges are skipped)
    assert stmts_by_ref["action_b"].depends_on == ["action_a"]  # B depends on A
    assert stmts_by_ref["action_c"].depends_on == ["action_b"]  # C depends on B


def test_build_action_statements_skips_disconnected_island():
    """Only trigger-connected actions should be converted to statements.

    Main DAG:
        trigger -> A -> B

    Disconnected island:
        C -> D
    """
    trigger_id = f"trigger-{WORKFLOW_UUID}"

    action_a = MockActionWithRef(
        id=UUID_A,
        type="udf",
        title="Action A",
        ref="action_a",
        upstream_edges=[
            {"source_id": trigger_id, "source_type": "trigger"},
        ],
    )
    action_b = MockActionWithRef(
        id=UUID_B,
        type="udf",
        title="Action B",
        ref="action_b",
        upstream_edges=[
            {
                "source_id": str(UUID_A),
                "source_type": "udf",
                "source_handle": "success",
            },
        ],
    )
    action_c = MockActionWithRef(
        id=UUID_C,
        type="udf",
        title="Action C",
        ref="action_c",
        upstream_edges=[],
    )
    action_d = MockActionWithRef(
        id=UUID_D,
        type="udf",
        title="Action D",
        ref="action_d",
        upstream_edges=[
            {
                "source_id": str(UUID_C),
                "source_type": "udf",
                "source_handle": "success",
            },
        ],
    )

    stmts = build_action_statements_from_actions(
        cast("list[Action]", [action_a, action_b, action_c, action_d])
    )
    stmts_by_ref = {stmt.ref: stmt for stmt in stmts}

    assert set(stmts_by_ref) == {"action_a", "action_b"}
    assert stmts_by_ref["action_a"].depends_on == []
    assert stmts_by_ref["action_b"].depends_on == ["action_a"]


def test_build_action_statements_no_trigger_edges_keeps_all_actions():
    """Legacy fallback: keep previous behavior if no trigger edges are present."""
    action_a = MockActionWithRef(
        id=UUID_A,
        type="udf",
        title="Action A",
        ref="action_a",
        upstream_edges=[],
    )
    action_b = MockActionWithRef(
        id=UUID_B,
        type="udf",
        title="Action B",
        ref="action_b",
        upstream_edges=[
            {
                "source_id": str(UUID_A),
                "source_type": "udf",
                "source_handle": "success",
            },
        ],
    )

    stmts = build_action_statements_from_actions(
        cast("list[Action]", [action_a, action_b])
    )
    stmts_by_ref = {stmt.ref: stmt for stmt in stmts}

    assert set(stmts_by_ref) == {"action_a", "action_b"}
    assert stmts_by_ref["action_a"].depends_on == []
    assert stmts_by_ref["action_b"].depends_on == ["action_a"]


# =============================================================================
# Tests for RFGraph validation
# =============================================================================


def test_validate_graph_empty_nodes():
    """Test that RFGraph raises error when nodes list is empty."""
    with pytest.raises(
        TracecatValidationError, match="Graph must have at least one node"
    ):
        RFGraph.model_validate({"nodes": [], "edges": []})


def test_validate_graph_no_trigger():
    """Test that RFGraph raises error when there is no trigger node."""
    rf_obj = {
        "nodes": [
            {
                "id": str(UUID_A),
                "type": "udf",
                "data": {"type": "udf"},
            },
        ],
        "edges": [],
    }

    with pytest.raises(TracecatValidationError, match="Expected 1 trigger node, got 0"):
        RFGraph.model_validate(rf_obj)


def test_validate_graph_multiple_triggers():
    """Test that RFGraph raises error when there are multiple trigger nodes."""
    rf_obj = {
        "nodes": [
            {
                "id": f"trigger-{WORKFLOW_UUID}",
                "type": "trigger",
                "data": {"title": "Trigger 1"},
            },
            {
                "id": f"trigger-{UUID_A}",
                "type": "trigger",
                "data": {"title": "Trigger 2"},
            },
        ],
        "edges": [],
    }

    with pytest.raises(TracecatValidationError, match="Expected 1 trigger node, got 2"):
        RFGraph.model_validate(rf_obj)


# =============================================================================
# Tests for RFGraph properties
# =============================================================================


def test_node_map(metadata):
    """Test that node_map correctly maps node IDs to nodes."""
    trigger_id = f"trigger-{WORKFLOW_UUID}"

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
        ],
        "edges": [
            {"source": trigger_id, "target": str(UUID_A)},
            {"source": str(UUID_A), "target": str(UUID_B)},
        ],
    }

    graph = RFGraph.model_validate(rf_obj)

    # Verify node_map contains all nodes
    assert len(graph.node_map) == 3

    # Verify trigger node is in map
    assert trigger_id in graph.node_map
    assert graph.node_map[trigger_id].type == "trigger"

    # Verify action nodes are in map
    assert UUID_A in graph.node_map
    assert graph.node_map[UUID_A].type == "udf"

    assert UUID_B in graph.node_map
    assert graph.node_map[UUID_B].type == "udf"


def test_indegree(metadata):
    """Test that indegree correctly counts incoming edges for action nodes.

       trigger -> A
                  /\\
                 B  C
                  \\/
                   D
    """
    trigger_id = f"trigger-{WORKFLOW_UUID}"

    rf_obj = {
        "nodes": [
            metadata.trigger,
            {"id": str(UUID_A), "type": "udf", "data": {"type": "udf"}},
            {"id": str(UUID_B), "type": "udf", "data": {"type": "udf"}},
            {"id": str(UUID_C), "type": "udf", "data": {"type": "udf"}},
            {"id": str(UUID_D), "type": "udf", "data": {"type": "udf"}},
        ],
        "edges": [
            {"source": trigger_id, "target": str(UUID_A)},
            {"source": str(UUID_A), "target": str(UUID_B)},
            {"source": str(UUID_A), "target": str(UUID_C)},
            {"source": str(UUID_B), "target": str(UUID_D)},
            {"source": str(UUID_C), "target": str(UUID_D)},
        ],
    }

    graph = RFGraph.model_validate(rf_obj)

    # A has indegree 0 (only trigger edge, which is excluded)
    assert graph.indegree[UUID_A] == 0

    # B and C each have indegree 1 (from A)
    assert graph.indegree[UUID_B] == 1
    assert graph.indegree[UUID_C] == 1

    # D has indegree 2 (from B and C)
    assert graph.indegree[UUID_D] == 2


def test_trigger_property(metadata):
    """Test that trigger property returns the trigger node."""
    rf_obj = {
        "nodes": [
            metadata.trigger,
            {"id": str(UUID_A), "type": "udf", "data": {"type": "udf"}},
        ],
        "edges": [
            {"source": metadata.trigger.id, "target": str(UUID_A)},
        ],
    }

    graph = RFGraph.model_validate(rf_obj)

    trigger = graph.trigger
    assert trigger.type == "trigger"
    assert trigger.id == metadata.trigger.id


# =============================================================================
# Tests for RFGraph.with_defaults
# =============================================================================


def test_with_defaults():
    """Test that with_defaults creates a valid graph with only a trigger node."""
    workflow = MockWorkflow(id=WORKFLOW_UUID, status="online")

    graph = RFGraph.with_defaults(cast("Workflow", workflow))

    # Should have exactly one node (trigger)
    assert len(graph.nodes) == 1
    assert graph.nodes[0].type == "trigger"
    assert graph.nodes[0].id == f"trigger-{WORKFLOW_UUID}"

    # Should have no edges
    assert len(graph.edges) == 0

    # Should have no action nodes
    assert len(graph.action_nodes()) == 0

    # Trigger should be accessible
    assert graph.trigger.id == f"trigger-{WORKFLOW_UUID}"


def test_with_defaults_trigger_data():
    """Test that with_defaults creates trigger with correct default data."""
    workflow = MockWorkflow(id=WORKFLOW_UUID)

    graph = RFGraph.with_defaults(cast("Workflow", workflow))

    trigger = graph.trigger
    assert trigger.data.title == "Trigger"


# =============================================================================
# Tests for RFGraph.normalize_action_ids
# =============================================================================
# Note: Additional tests for normalize_action_ids are in test_dsl_common.py.
# Since UDFNode.id is typed as ActionID (uuid.UUID), node IDs are always
# valid UUIDs after construction. The normalize_action_ids method is a no-op
# for graphs with valid UUID node IDs.


def test_normalize_action_ids_no_normalization_needed(metadata):
    """Test normalize_action_ids when IDs are already in canonical format."""
    trigger_id = f"trigger-{WORKFLOW_UUID}"

    rf_obj = {
        "nodes": [
            metadata.trigger,
            {"id": str(UUID_A), "type": "udf", "data": {"type": "udf"}},
            {"id": str(UUID_B), "type": "udf", "data": {"type": "udf"}},
        ],
        "edges": [
            {"source": trigger_id, "target": str(UUID_A)},
            {"source": str(UUID_A), "target": str(UUID_B)},
        ],
    }

    graph = RFGraph.model_validate(rf_obj)
    normalized = graph.normalize_action_ids()

    # Should return same graph (no changes needed)
    assert normalized is graph
