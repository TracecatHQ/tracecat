"""Test scope validation for scatter-gather operations in DSL workflows."""

import pytest

from tracecat.dsl.common import DSLEntrypoint, DSLInput
from tracecat.dsl.models import ActionStatement
from tracecat.types.exceptions import TracecatDSLError


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
            match="Action 'invalid_action_ref' depends on 'process_item' which cannot be referenced from this scope",
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
            match="Action 'process_item2' depends on 'scatter_items2', which is in a different scope",
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
            match="Action 'c' depends on 'b', which is in a different scope",
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
