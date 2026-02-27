"""Test scope validation for scatter-gather operations in DSL workflows."""

import pytest

from tracecat.dsl.common import DSLEntrypoint, DSLInput
from tracecat.dsl.schemas import ActionStatement
from tracecat.exceptions import TracecatDSLError


class TestScopeValidation:
    """Test scope region validation for scatter-gather patterns."""

    def test_valid_scatter_gather_workflow(self):
        """Test a valid scatter-gather workflow passes validation."""
        dsl = DSLInput(
            title="Valid Scatter-Gather",
            description="A workflow with proper scatter-gather scoping",
            entrypoint=DSLEntrypoint(),
            actions=[
                ActionStatement(  # scope: root
                    ref="prepare_data",
                    action="core.transform.reshape",
                    args={"value": [1, 2, 3]},
                ),
                ActionStatement(  # scope: scatter_items (scatter region)
                    ref="scatter_items",
                    action="core.transform.scatter",
                    args={"collection": "${{ ACTIONS.prepare_data.result }}"},
                    depends_on=["prepare_data"],
                ),
                ActionStatement(  # scope: scatter_items (scatter region)
                    ref="process_item",
                    action="core.transform.reshape",
                    args={"value": "${{ TRIGGER.item * 2 }}"},
                    depends_on=["scatter_items"],
                ),
                ActionStatement(  # scope: root
                    ref="gather_results",
                    action="core.transform.gather",
                    args={"items": "${{ ACTIONS.process_item.result }}"},
                    depends_on=["process_item"],
                ),
                ActionStatement(  # scope: root
                    ref="final_report",
                    action="core.transform.reshape",
                    args={"value": "${{ ACTIONS.gather_results.result }}"},
                    depends_on=["gather_results"],
                ),
            ],
        )

        # Should not raise any exceptions
        assert dsl is not None

    def test_invalid_upward_reference(self):
        """Test that upward references (outer -> inner) are caught."""
        with pytest.raises(
            TracecatDSLError,
            match="Action 'invalid_action_ref' has an expression in field 'inputs' that references 'process_item' which cannot be referenced from this scope",
        ):
            DSLInput(
                title="Invalid Upward Reference",
                description="The graph is structurally sound, but invalid_action_ref references an inner scope",
                entrypoint=DSLEntrypoint(),
                actions=[
                    ActionStatement(  # scope: scatter_items (scatter region)
                        ref="scatter_items",
                        action="core.transform.scatter",
                        args={"collection": "${{ [1, 2, 3] }}"},
                    ),
                    ActionStatement(  # scope: scatter_items (scatter region)
                        ref="process_item",
                        action="core.transform.reshape",
                        args={"value": "${{ ACTIONS.scatter_items.result * 2 }}"},
                        depends_on=["scatter_items"],
                    ),
                    ActionStatement(  # scope: root
                        ref="gather_results",
                        action="core.transform.gather",
                        args={"items": "${{ ACTIONS.process_item.result }}"},
                        depends_on=["process_item"],
                    ),
                    ActionStatement(  # scope: root
                        ref="invalid_action_ref",
                        action="core.transform.reshape",
                        # XXX: outer scope referencing inner scope
                        args={"value": "${{ ACTIONS.process_item.result }}"},
                        depends_on=["gather_results"],
                    ),
                ],
            )

    def test_valid_reference_from_inner_to_outer(self):
        """
        Test that referencing an outer scope from an inner scope is allowed.

        This test creates a workflow with the following structure:
            a -> scatter -> b -> gather
        where 'b' references 'a' in its arguments. This should pass validation.
        """
        dsl = DSLInput(
            title="Valid Reference from Inner to Outer",
            description="A workflow where an inner action references an outer action",
            entrypoint=DSLEntrypoint(),
            actions=[
                ActionStatement(  # scope: root
                    ref="a",
                    action="core.transform.reshape",
                    args={"value": [1, 2, 3]},
                ),
                ActionStatement(  # scope: scatter_items (scatter region)
                    ref="scatter_items",
                    action="core.transform.scatter",
                    args={"collection": "${{ ACTIONS.a.result }}"},
                    depends_on=["a"],
                ),
                ActionStatement(  # scope: scatter_items (scatter region)
                    ref="b",
                    action="core.transform.reshape",
                    # b references a (outer scope) in its args
                    args={"value": "${{ ACTIONS.a.result }}"},
                    depends_on=["scatter_items"],
                ),
                ActionStatement(  # scope: root
                    ref="gather",
                    action="core.transform.gather",
                    args={"items": "${{ ACTIONS.b.result }}"},
                    depends_on=["b"],
                ),
            ],
        )
        # Should not raise any exceptions
        assert dsl is not None

    def test_cross_region_dependency(self):
        """Test that cross-region dependencies are caught."""
        with pytest.raises(
            TracecatDSLError,
            match="Action 'process_item2' has an edge from 'scatter_items2', which is in a different scatter-gather scope",
        ):
            DSLInput(
                title="Cross-Region Dependency",
                description="A workflow with cross-region dependencies",
                entrypoint=DSLEntrypoint(),
                actions=[
                    ActionStatement(  # scope: root
                        ref="prepare_data1",
                        action="core.transform.reshape",
                        args={"value": [1, 2, 3]},
                        depends_on=[],
                    ),
                    ActionStatement(  # scope: root
                        ref="prepare_data2",
                        action="core.transform.reshape",
                        args={"value": [4, 5, 6]},
                        depends_on=[],
                    ),
                    ActionStatement(  # scope: scatter_items1 (scatter region 1)
                        ref="scatter_items1",
                        action="core.transform.scatter",
                        args={"collection": "${{ ACTIONS.prepare_data1.result }}"},
                        depends_on=["prepare_data1"],
                    ),
                    ActionStatement(  # scope: scatter_items2 (scatter region 2)
                        ref="scatter_items2",
                        action="core.transform.scatter",
                        args={"collection": "${{ ACTIONS.prepare_data2.result }}"},
                        depends_on=["prepare_data2"],
                    ),
                    ActionStatement(  # scope: scatter_items1 (scatter region 1)
                        ref="process_item1",
                        action="core.transform.reshape",
                        args={"value": "${{ TRIGGER.item * 2 }}"},
                        depends_on=["scatter_items1"],
                    ),
                    ActionStatement(  # scope: scatter_items2 (scatter region 2, invalid: cross-region reference)
                        ref="process_item2",
                        action="core.transform.reshape",
                        args={
                            "value": "${{ ACTIONS.process_item1.result }}"
                        },  # Invalid: cross-region reference
                        depends_on=[
                            "scatter_items2",
                            "process_item1",
                        ],  # This should be caught
                    ),
                ],
            )

    def test_nested_scatter_gather(self):
        """Test nested scatter-gather operations."""
        dsl = DSLInput(
            title="Nested Scatter-Gather",
            description="A workflow with nested scatter-gather operations",
            entrypoint=DSLEntrypoint(),
            actions=[
                ActionStatement(  # scope: root
                    ref="prepare_data",
                    action="core.transform.reshape",
                    args={"value": [[1, 2], [3, 4]]},
                    depends_on=[],
                ),
                ActionStatement(  # scope: outer_scatter (outer scatter region)
                    ref="outer_scatter",
                    action="core.transform.scatter",
                    args={"collection": "${{ ACTIONS.prepare_data.result }}"},
                    depends_on=["prepare_data"],
                ),
                ActionStatement(  # scope: inner_scatter (nested scatter region)
                    ref="inner_scatter",
                    action="core.transform.scatter",
                    args={"collection": "${{ TRIGGER.item }}"},
                    depends_on=["outer_scatter"],
                ),
                ActionStatement(  # scope: inner_scatter (nested scatter region)
                    ref="process_inner_item",
                    action="core.transform.reshape",
                    args={"value": "${{ TRIGGER.item * 10 }}"},
                    depends_on=["inner_scatter"],
                ),
                ActionStatement(  # scope: inner_scatter (nested scatter region)
                    ref="inner_gather",
                    action="core.transform.gather",
                    args={"items": "${{ ACTIONS.process_inner_item.result }}"},
                    depends_on=["process_inner_item"],
                ),
                ActionStatement(  # scope: outer_scatter (outer scatter region)
                    ref="outer_gather",
                    action="core.transform.gather",
                    args={"items": "${{ ACTIONS.inner_gather.result }}"},
                    depends_on=["inner_gather"],
                ),
            ],
        )

        # Should not raise any exceptions
        assert dsl is not None

    def test_workflow_without_scatter_gather(self):
        """Test that regular workflows without scatter-gather work normally."""
        dsl = DSLInput(
            title="Regular Workflow",
            description="A normal workflow without scatter-gather",
            entrypoint=DSLEntrypoint(),
            actions=[
                ActionStatement(  # scope: root
                    ref="action_a",
                    action="core.transform.reshape",
                    args={"value": 1},
                    depends_on=[],
                ),
                ActionStatement(  # scope: root
                    ref="action_b",
                    action="core.transform.reshape",
                    args={"value": "${{ ACTIONS.action_a.result + 1 }}"},
                    depends_on=["action_a"],
                ),
                ActionStatement(  # scope: root
                    ref="action_c",
                    action="core.transform.reshape",
                    args={"value": "${{ ACTIONS.action_b.result + 1 }}"},
                    depends_on=["action_b"],
                ),
            ],
        )

        # Should not raise any exceptions
        assert dsl is not None

    def test_scatter_without_gather(self):
        """Test scatter without corresponding gather."""
        dsl = DSLInput(
            title="Scatter Without Gather",
            description="A workflow with scatter but no gather",
            entrypoint=DSLEntrypoint(),
            actions=[
                ActionStatement(  # scope: root
                    ref="prepare_data",
                    action="core.transform.reshape",
                    args={"value": [1, 2, 3]},
                    depends_on=[],
                ),
                ActionStatement(  # scope: scatter_items (scatter region)
                    ref="scatter_items",
                    action="core.transform.scatter",
                    args={"collection": "${{ ACTIONS.prepare_data.result }}"},
                    depends_on=["prepare_data"],
                ),
                ActionStatement(  # scope: scatter_items (scatter region)
                    ref="process_item",
                    action="core.transform.reshape",
                    args={"value": "${{ TRIGGER.item * 2 }}"},
                    depends_on=["scatter_items"],
                ),
                # No gather - this should still be valid
            ],
        )

        # Should not raise any exceptions
        assert dsl is not None

    def test_gather_without_scatter(self):
        """
        Test that a gather action without a corresponding scatter raises an error.

        A gather action must always be paired with a scatter action. This test ensures
        that a gather without a scatter is not allowed and raises a TracecatDSLError.
        """
        with pytest.raises(
            TracecatDSLError,
            match="There are more gather actions than scatter actions.",
        ):
            DSLInput(
                title="Gather Without Scatter",
                description="A workflow with gather but no scatter",
                entrypoint=DSLEntrypoint(),
                actions=[
                    ActionStatement(  # scope: root
                        ref="prepare_data",
                        action="core.transform.reshape",
                        args={"value": [1, 2, 3]},
                        depends_on=[],
                    ),
                    ActionStatement(  # scope: root
                        ref="gather_results",
                        action="core.transform.gather",
                        args={"items": "${{ ACTIONS.prepare_data.result }}"},
                        depends_on=["prepare_data"],
                    ),
                ],
            )

    def test_dependencies_cross_scopes_are_invalid(self):
        """
        Test that an action depending on actions from different scopes raises an error.

        Diagram:
            A
            |
         +-------+
         |Scatter|
         |   |   |
         |   B   |
         +---|---+
             |
          Gather
           /  \
          v    v
          C <--+
        (C depends on both B (inner scope) and Gather (outer scope))
        """
        with pytest.raises(
            TracecatDSLError,
            match="Action 'c' has an edge from 'b', which is in a different scatter-gather scope",
        ):
            DSLInput(
                title="Dependencies Cross Scopes",
                description="A workflow where an action depends on both an inner and an outer scope action.",
                entrypoint=DSLEntrypoint(),
                actions=[
                    ActionStatement(  # scope: root
                        ref="a",
                        action="core.transform.reshape",
                        args={"value": [1, 2, 3]},
                        depends_on=[],
                    ),
                    ActionStatement(  # scope: scatter (scatter region)
                        ref="scatter",
                        action="core.transform.scatter",
                        args={"collection": "${{ ACTIONS.a.result }}"},
                        depends_on=["a"],
                    ),
                    ActionStatement(  # scope: scatter (scatter region)
                        ref="b",
                        action="core.transform.reshape",
                        args={"value": "${{ TRIGGER.item * 2 }}"},
                        depends_on=["scatter"],
                    ),
                    ActionStatement(  # scope: root
                        ref="gather",
                        action="core.transform.gather",
                        args={"items": "${{ ACTIONS.b.result }}"},
                        depends_on=["b"],
                    ),
                    ActionStatement(  # scope: root
                        ref="c",
                        action="core.transform.reshape",
                        # c depends on both b (inner scope) and gather (outer scope)
                        args={
                            "value": "${{ ACTIONS.b.result + ACTIONS.gather.result }}"
                        },
                        depends_on=["b", "gather"],
                    ),
                ],
            )

    def test_valid_loop_scope(self):
        """Test a valid do-while scope passes validation."""
        dsl = DSLInput(
            title="Valid loop scope",
            description="A workflow with core.loop.start/core.loop.end scope",
            entrypoint=DSLEntrypoint(),
            actions=[
                ActionStatement(
                    ref="seed",
                    action="core.transform.reshape",
                    args={"value": 1},
                ),
                ActionStatement(
                    ref="loop_start",
                    action="core.loop.start",
                    depends_on=["seed"],
                ),
                ActionStatement(
                    ref="step",
                    action="core.transform.reshape",
                    depends_on=["loop_start"],
                    args={"value": "${{ ACTIONS.loop_start.result.iteration }}"},
                ),
                ActionStatement(
                    ref="loop_end",
                    action="core.loop.end",
                    depends_on=["step"],
                    args={
                        "condition": "${{ ACTIONS.loop_start.result.iteration < 1 }}",
                        "max_iterations": 5,
                    },
                ),
            ],
        )

        assert dsl is not None

    def test_loop_end_condition_rejects_sibling_scatter_scope_reference(self):
        """Loop end condition cannot read from a sibling scatter branch."""
        with pytest.raises(
            TracecatDSLError,
            match="condition refs must be in loop scope",
        ):
            DSLInput(
                title="Loop end rejects sibling scatter ref",
                description="Loop condition cannot read sibling scatter action",
                entrypoint=DSLEntrypoint(),
                actions=[
                    ActionStatement(
                        ref="seed",
                        action="core.transform.reshape",
                        args={"value": [1, 2]},
                    ),
                    ActionStatement(
                        ref="scatter",
                        action="core.transform.scatter",
                        depends_on=["seed"],
                        args={"collection": "${{ ACTIONS.seed.result }}"},
                    ),
                    ActionStatement(
                        ref="per_item",
                        action="core.transform.reshape",
                        depends_on=["scatter"],
                        args={"value": "${{ ACTIONS.scatter.result }}"},
                    ),
                    ActionStatement(
                        ref="gather",
                        action="core.transform.gather",
                        depends_on=["per_item"],
                        args={"items": "${{ ACTIONS.per_item.result }}"},
                    ),
                    ActionStatement(
                        ref="loop_start",
                        action="core.loop.start",
                        depends_on=["seed"],
                    ),
                    ActionStatement(
                        ref="loop_body",
                        action="core.transform.reshape",
                        depends_on=["loop_start"],
                        args={"value": "${{ ACTIONS.loop_start.result.iteration }}"},
                    ),
                    ActionStatement(
                        ref="loop_end",
                        action="core.loop.end",
                        depends_on=["loop_body"],
                        args={"condition": "${{ ACTIONS.per_item.result > 0 }}"},
                    ),
                ],
            )

    def test_loop_end_condition_allows_same_loop_scope_reference(self):
        """Loop end condition can read actions in the closed loop scope."""
        dsl = DSLInput(
            title="Loop end allows loop scope ref",
            description="Loop condition reads loop-body action",
            entrypoint=DSLEntrypoint(),
            actions=[
                ActionStatement(
                    ref="seed",
                    action="core.transform.reshape",
                    args={"value": 1},
                ),
                ActionStatement(
                    ref="loop_start",
                    action="core.loop.start",
                    depends_on=["seed"],
                ),
                ActionStatement(
                    ref="loop_body",
                    action="core.transform.reshape",
                    depends_on=["loop_start"],
                    args={"value": "${{ ACTIONS.loop_start.result.iteration }}"},
                ),
                ActionStatement(
                    ref="loop_end",
                    action="core.loop.end",
                    depends_on=["loop_body"],
                    args={"condition": "${{ ACTIONS.loop_body.result >= 0 }}"},
                ),
            ],
        )

        assert dsl is not None

    def test_loop_end_condition_rejects_nested_scatter_item_reference(self):
        """Loop end condition cannot read per-item action in nested scatter scope."""
        with pytest.raises(
            TracecatDSLError,
            match="condition refs must be in loop scope",
        ):
            DSLInput(
                title="Loop end rejects nested scatter item ref",
                description="Loop condition cannot read nested per-item action",
                entrypoint=DSLEntrypoint(),
                actions=[
                    ActionStatement(
                        ref="seed",
                        action="core.transform.reshape",
                        args={"value": [1, 2]},
                    ),
                    ActionStatement(
                        ref="loop_start",
                        action="core.loop.start",
                        depends_on=["seed"],
                    ),
                    ActionStatement(
                        ref="items",
                        action="core.transform.reshape",
                        depends_on=["loop_start"],
                        args={"value": "${{ ACTIONS.seed.result }}"},
                    ),
                    ActionStatement(
                        ref="scatter",
                        action="core.transform.scatter",
                        depends_on=["items"],
                        args={"collection": "${{ ACTIONS.items.result }}"},
                    ),
                    ActionStatement(
                        ref="per_item",
                        action="core.transform.reshape",
                        depends_on=["scatter"],
                        args={"value": "${{ ACTIONS.scatter.result }}"},
                    ),
                    ActionStatement(
                        ref="gather",
                        action="core.transform.gather",
                        depends_on=["per_item"],
                        args={"items": "${{ ACTIONS.per_item.result }}"},
                    ),
                    ActionStatement(
                        ref="loop_end",
                        action="core.loop.end",
                        depends_on=["gather"],
                        args={"condition": "${{ ACTIONS.per_item.result > 0 }}"},
                    ),
                ],
            )

    def test_loop_end_condition_allows_loop_scope_gather_reference(self):
        """Loop end condition can read synchronized gather output in loop scope."""
        dsl = DSLInput(
            title="Loop end allows loop-scope gather ref",
            description="Loop condition reads gather output in loop scope",
            entrypoint=DSLEntrypoint(),
            actions=[
                ActionStatement(
                    ref="seed",
                    action="core.transform.reshape",
                    args={"value": [1, 2]},
                ),
                ActionStatement(
                    ref="loop_start",
                    action="core.loop.start",
                    depends_on=["seed"],
                ),
                ActionStatement(
                    ref="items",
                    action="core.transform.reshape",
                    depends_on=["loop_start"],
                    args={"value": "${{ ACTIONS.seed.result }}"},
                ),
                ActionStatement(
                    ref="scatter",
                    action="core.transform.scatter",
                    depends_on=["items"],
                    args={"collection": "${{ ACTIONS.items.result }}"},
                ),
                ActionStatement(
                    ref="per_item",
                    action="core.transform.reshape",
                    depends_on=["scatter"],
                    args={"value": "${{ ACTIONS.scatter.result }}"},
                ),
                ActionStatement(
                    ref="gather",
                    action="core.transform.gather",
                    depends_on=["per_item"],
                    args={"items": "${{ ACTIONS.per_item.result }}"},
                ),
                ActionStatement(
                    ref="loop_end",
                    action="core.loop.end",
                    depends_on=["gather"],
                    args={"condition": "${{ ACTIONS.gather.result != None }}"},
                ),
            ],
        )

        assert dsl is not None

    def test_loop_end_condition_rejects_ancestor_scope_reference(self):
        """Loop end condition cannot read from an ancestor/root action."""
        with pytest.raises(
            TracecatDSLError,
            match="condition refs must be in loop scope",
        ):
            DSLInput(
                title="Loop end rejects root scope ref",
                description="Loop condition cannot read root action",
                entrypoint=DSLEntrypoint(),
                actions=[
                    ActionStatement(
                        ref="seed",
                        action="core.transform.reshape",
                        args={"value": 1},
                    ),
                    ActionStatement(
                        ref="loop_start",
                        action="core.loop.start",
                        depends_on=["seed"],
                    ),
                    ActionStatement(
                        ref="loop_body",
                        action="core.transform.reshape",
                        depends_on=["loop_start"],
                        args={"value": "${{ ACTIONS.loop_start.result.iteration }}"},
                    ),
                    ActionStatement(
                        ref="loop_end",
                        action="core.loop.end",
                        depends_on=["loop_body"],
                        args={"condition": "${{ ACTIONS.seed.result > 0 }}"},
                    ),
                ],
            )

    def test_outer_loop_end_condition_rejects_inner_loop_reference(self):
        """Outer loop condition cannot read inner-loop action output."""
        with pytest.raises(
            TracecatDSLError,
            match="condition refs must be in loop scope",
        ):
            DSLInput(
                title="Outer loop rejects inner loop ref",
                description="Outer loop condition cannot read inner body action",
                entrypoint=DSLEntrypoint(),
                actions=[
                    ActionStatement(
                        ref="seed",
                        action="core.transform.reshape",
                        args={"value": 1},
                    ),
                    ActionStatement(
                        ref="outer_start",
                        action="core.loop.start",
                        depends_on=["seed"],
                    ),
                    ActionStatement(
                        ref="inner_start",
                        action="core.loop.start",
                        depends_on=["outer_start"],
                    ),
                    ActionStatement(
                        ref="inner_body",
                        action="core.transform.reshape",
                        depends_on=["inner_start"],
                        args={"value": "${{ ACTIONS.inner_start.result.iteration }}"},
                    ),
                    ActionStatement(
                        ref="inner_end",
                        action="core.loop.end",
                        depends_on=["inner_body"],
                        args={"condition": "${{ False }}"},
                    ),
                    ActionStatement(
                        ref="outer_body",
                        action="core.transform.reshape",
                        depends_on=["inner_end"],
                        args={"value": "${{ ACTIONS.inner_end.result.continue }}"},
                    ),
                    ActionStatement(
                        ref="outer_end",
                        action="core.loop.end",
                        depends_on=["outer_body"],
                        args={"condition": "${{ ACTIONS.inner_body.result > 0 }}"},
                    ),
                ],
            )

    def test_loop_end_requires_single_dependency_scope(self):
        """Loop end must not depend on multiple distinct dependency scopes."""
        with pytest.raises(
            TracecatDSLError,
            match="must depend on actions from exactly one loop scope",
        ):
            DSLInput(
                title="Loop end multiple dep scopes invalid",
                description="Loop end cannot close from multiple scopes",
                entrypoint=DSLEntrypoint(),
                actions=[
                    ActionStatement(
                        ref="seed",
                        action="core.transform.reshape",
                        args={"value": 1},
                    ),
                    ActionStatement(
                        ref="loop_start",
                        action="core.loop.start",
                        depends_on=["seed"],
                    ),
                    ActionStatement(
                        ref="loop_body",
                        action="core.transform.reshape",
                        depends_on=["loop_start"],
                        args={"value": "${{ ACTIONS.loop_start.result.iteration }}"},
                    ),
                    ActionStatement(
                        ref="loop_end",
                        action="core.loop.end",
                        depends_on=["loop_body", "seed"],
                        args={"condition": "${{ False }}"},
                    ),
                ],
            )

    def test_outer_scope_can_reference_loop_descendant_expression(self):
        """Test parent scope can reference loop-body expression after loop closes."""
        dsl = DSLInput(
            title="Loop descendant reference",
            description="Root action references final loop-body result",
            entrypoint=DSLEntrypoint(),
            actions=[
                ActionStatement(
                    ref="seed",
                    action="core.transform.reshape",
                    args={"value": 1},
                ),
                ActionStatement(
                    ref="loop_start",
                    action="core.loop.start",
                    depends_on=["seed"],
                ),
                ActionStatement(
                    ref="body",
                    action="core.transform.reshape",
                    depends_on=["loop_start"],
                    args={"value": "${{ ACTIONS.loop_start.result.iteration }}"},
                ),
                ActionStatement(
                    ref="loop_end",
                    action="core.loop.end",
                    depends_on=["body"],
                    args={
                        "condition": "${{ ACTIONS.loop_start.result.iteration < 1 }}"
                    },
                ),
                ActionStatement(
                    ref="after",
                    action="core.transform.reshape",
                    depends_on=["loop_end"],
                    args={"value": "${{ ACTIONS.body.result }}"},
                ),
            ],
        )

        assert dsl is not None

    def test_loop_scope_requires_synchronization_at_loop_end(self):
        """Loop body branches must converge at loop_end.

        This prevents the scheduler from continuing to the next iteration while a
        sibling in-loop branch is still running and mutating ACTIONS.
        """
        with pytest.raises(
            TracecatDSLError,
            match="must synchronize at 'loop_end'",
        ):
            DSLInput(
                title="Loop branch unsynchronized",
                description="A loop body branch does not flow into loop_end",
                entrypoint=DSLEntrypoint(),
                actions=[
                    ActionStatement(
                        ref="seed",
                        action="core.transform.reshape",
                        args={"value": 1},
                    ),
                    ActionStatement(
                        ref="loop_start",
                        action="core.loop.start",
                        depends_on=["seed"],
                    ),
                    ActionStatement(
                        ref="fast",
                        action="core.transform.reshape",
                        depends_on=["loop_start"],
                        args={"value": "fast"},
                    ),
                    ActionStatement(
                        ref="slow",
                        action="core.transform.reshape",
                        depends_on=["loop_start"],
                        args={"value": "slow"},
                    ),
                    ActionStatement(
                        ref="loop_end",
                        action="core.loop.end",
                        depends_on=["fast"],
                        args={"condition": "${{ True }}"},
                    ),
                ],
            )

    def test_loop_scope_with_scatter_requires_synchronization_at_loop_end(self):
        """Scatter work inside a loop must also converge before loop_end."""
        with pytest.raises(
            TracecatDSLError,
            match="must synchronize at 'loop_end'",
        ):
            DSLInput(
                title="Loop scatter unsynchronized",
                description="Scatter/gather branch in loop does not feed loop_end",
                entrypoint=DSLEntrypoint(),
                actions=[
                    ActionStatement(
                        ref="seed",
                        action="core.transform.reshape",
                        args={"value": [1, 2]},
                    ),
                    ActionStatement(
                        ref="loop_start",
                        action="core.loop.start",
                        depends_on=["seed"],
                    ),
                    ActionStatement(
                        ref="items",
                        action="core.transform.reshape",
                        depends_on=["loop_start"],
                        args={"value": [1, 2]},
                    ),
                    ActionStatement(
                        ref="scatter",
                        action="core.transform.scatter",
                        depends_on=["items"],
                        args={"collection": "${{ ACTIONS.items.result }}"},
                    ),
                    ActionStatement(
                        ref="per_item",
                        action="core.transform.reshape",
                        depends_on=["scatter"],
                        args={"value": "${{ ACTIONS.scatter.result }}"},
                    ),
                    ActionStatement(
                        ref="gather",
                        action="core.transform.gather",
                        depends_on=["per_item"],
                        args={"items": "${{ ACTIONS.per_item.result }}"},
                    ),
                    ActionStatement(
                        ref="loop_end",
                        action="core.loop.end",
                        depends_on=["loop_start"],
                        args={"condition": "${{ False }}"},
                    ),
                ],
            )

    def test_loop_scope_synchronization_passes_when_all_branches_join_loop_end(self):
        """A loop with fan-out/fan-in is valid when all branches feed loop_end."""
        dsl = DSLInput(
            title="Loop synchronized fan-in",
            description="All loop branches are synchronized at loop_end",
            entrypoint=DSLEntrypoint(),
            actions=[
                ActionStatement(
                    ref="seed",
                    action="core.transform.reshape",
                    args={"value": 1},
                ),
                ActionStatement(
                    ref="loop_start",
                    action="core.loop.start",
                    depends_on=["seed"],
                ),
                ActionStatement(
                    ref="left",
                    action="core.transform.reshape",
                    depends_on=["loop_start"],
                    args={"value": "left"},
                ),
                ActionStatement(
                    ref="right",
                    action="core.transform.reshape",
                    depends_on=["loop_start"],
                    args={"value": "right"},
                ),
                ActionStatement(
                    ref="loop_end",
                    action="core.loop.end",
                    depends_on=["left", "right"],
                    args={"condition": "${{ False }}"},
                ),
            ],
        )

        assert dsl is not None

    def test_outer_scope_cannot_reference_scatter_descendant_expression(self):
        """Test parent scope still cannot reference scatter descendant expression."""
        with pytest.raises(
            TracecatDSLError,
            match=(
                "Action 'after' has an expression in field 'inputs' "
                "that references 'step' which cannot be referenced from this scope"
            ),
        ):
            DSLInput(
                title="Scatter descendant reference invalid",
                description="Root action cannot reference action nested under scatter scope",
                entrypoint=DSLEntrypoint(),
                actions=[
                    ActionStatement(
                        ref="scatter",
                        action="core.transform.scatter",
                        args={"collection": "${{ [1, 2] }}"},
                    ),
                    ActionStatement(
                        ref="loop_start",
                        action="core.loop.start",
                        depends_on=["scatter"],
                    ),
                    ActionStatement(
                        ref="step",
                        action="core.transform.reshape",
                        depends_on=["loop_start"],
                        args={"value": "${{ ACTIONS.scatter.result }}"},
                    ),
                    ActionStatement(
                        ref="loop_end",
                        action="core.loop.end",
                        depends_on=["step"],
                        args={"condition": "${{ False }}"},
                    ),
                    ActionStatement(
                        ref="gather",
                        action="core.transform.gather",
                        depends_on=["loop_end"],
                        args={"items": "${{ ACTIONS.loop_end.result }}"},
                    ),
                    ActionStatement(
                        ref="after",
                        action="core.transform.reshape",
                        depends_on=["gather"],
                        args={"value": "${{ ACTIONS.step.result }}"},
                    ),
                ],
            )

    def test_loop_end_without_loop_start(self):
        """Test that loop.end without loop.start raises an error."""
        with pytest.raises(
            TracecatDSLError,
            match="Loop scopes must be balanced:",
        ):
            DSLInput(
                title="Loop end without start",
                description="Invalid loop closer without opener",
                entrypoint=DSLEntrypoint(),
                actions=[
                    ActionStatement(
                        ref="step",
                        action="core.transform.reshape",
                        args={"value": 1},
                    ),
                    ActionStatement(
                        ref="loop_end",
                        action="core.loop.end",
                        depends_on=["step"],
                        args={"condition": "${{ False }}"},
                    ),
                ],
            )

    def test_loop_start_without_loop_end(self):
        """Test that loop.start without loop.end raises an error."""
        with pytest.raises(
            TracecatDSLError,
            match="Loop scopes must be balanced:",
        ):
            DSLInput(
                title="Loop start without end",
                description="Invalid loop opener without closer",
                entrypoint=DSLEntrypoint(),
                actions=[
                    ActionStatement(
                        ref="seed",
                        action="core.transform.reshape",
                        args={"value": 1},
                    ),
                    ActionStatement(
                        ref="loop_start",
                        action="core.loop.start",
                        depends_on=["seed"],
                    ),
                    ActionStatement(
                        ref="step",
                        action="core.transform.reshape",
                        depends_on=["loop_start"],
                        args={"value": "${{ ACTIONS.loop_start.result.iteration }}"},
                    ),
                ],
            )

    def test_edge_crossing_from_loop_scope_to_root_is_invalid(self):
        """Test that root action cannot depend on both loop-inner and root actions."""
        with pytest.raises(
            TracecatDSLError,
            match=(
                "Action 'after' has an edge from 'body', "
                "which is in a different scatter-gather scope"
            ),
        ):
            DSLInput(
                title="Cross-scope loop edge invalid",
                description="Root action edge cannot cross loop boundary",
                entrypoint=DSLEntrypoint(),
                actions=[
                    ActionStatement(
                        ref="seed",
                        action="core.transform.reshape",
                        args={"value": 1},
                    ),
                    ActionStatement(
                        ref="loop_start",
                        action="core.loop.start",
                        depends_on=["seed"],
                    ),
                    ActionStatement(
                        ref="body",
                        action="core.transform.reshape",
                        depends_on=["loop_start"],
                        args={"value": "${{ ACTIONS.loop_start.result.iteration }}"},
                    ),
                    ActionStatement(
                        ref="loop_end",
                        action="core.loop.end",
                        depends_on=["body"],
                        args={"condition": "${{ False }}"},
                    ),
                    ActionStatement(
                        ref="after",
                        action="core.transform.reshape",
                        depends_on=["loop_end", "body"],
                        args={"value": "${{ ACTIONS.loop_end.result.continue }}"},
                    ),
                ],
            )

    def test_valid_nested_loop_inside_scatter(self):
        """Test loop nested inside scatter scope."""
        dsl = DSLInput(
            title="Loop in scatter",
            description="Nested loop scope inside scatter scope",
            entrypoint=DSLEntrypoint(),
            actions=[
                ActionStatement(
                    ref="scatter",
                    action="core.transform.scatter",
                    args={"collection": "${{ [1, 2] }}"},
                ),
                ActionStatement(
                    ref="loop_start",
                    action="core.loop.start",
                    depends_on=["scatter"],
                ),
                ActionStatement(
                    ref="step",
                    action="core.transform.reshape",
                    depends_on=["loop_start"],
                    args={"value": "${{ ACTIONS.scatter.result }}"},
                ),
                ActionStatement(
                    ref="loop_end",
                    action="core.loop.end",
                    depends_on=["step"],
                    args={"condition": "${{ False }}"},
                ),
                ActionStatement(
                    ref="gather",
                    action="core.transform.gather",
                    depends_on=["loop_end"],
                    args={"items": "${{ ACTIONS.loop_end.result }}"},
                ),
            ],
        )

        assert dsl is not None
