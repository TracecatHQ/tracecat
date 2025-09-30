"""Unit tests for scatter interval model changes.

Tests the Task.delay and ScatterArgs.interval fields added for the scatter
interval feature.
"""

from tracecat.dsl.models import ScatterArgs, StreamID, Task


class TestTaskModel:
    """Test Task model with delay field."""

    def test_task_without_delay(self):
        """Test Task can be created without delay (default 0.0)."""
        task = Task(ref="test_task", stream_id=StreamID("root:0"))
        assert task.ref == "test_task"
        assert task.delay == 0.0

    def test_task_with_delay(self):
        """Test Task can be created with delay."""
        task = Task(
            ref="test_task",
            stream_id=StreamID("root:0"),
            delay=1.5,
        )
        assert task.ref == "test_task"
        assert task.delay == 1.5

    def test_task_with_zero_delay(self):
        """Test Task can have zero delay."""
        task = Task(
            ref="test_task",
            stream_id=StreamID("root:0"),
            delay=0.0,
        )
        assert task.delay == 0.0

    def test_task_delay_negative_value(self):
        """Test Task with negative delay is allowed (validation happens elsewhere)."""
        # Note: The model doesn't enforce positive values, but the scheduler should
        task = Task(
            ref="test_task",
            stream_id=StreamID("root:0"),
            delay=-1.0,
        )
        assert task.delay == -1.0


class TestScatterArgsModel:
    """Test ScatterArgs model with interval field."""

    def test_scatter_args_without_interval(self):
        """Test ScatterArgs can be created without interval (default None)."""
        args = ScatterArgs(collection=[1, 2, 3])
        assert args.collection == [1, 2, 3]
        assert args.interval is None

    def test_scatter_args_with_interval(self):
        """Test ScatterArgs can be created with interval."""
        args = ScatterArgs(collection=[1, 2, 3], interval=0.5)
        assert args.collection == [1, 2, 3]
        assert args.interval == 0.5

    def test_scatter_args_with_zero_interval(self):
        """Test ScatterArgs can have zero interval."""
        args = ScatterArgs(collection=[1, 2, 3], interval=0)
        assert args.interval == 0

    def test_scatter_args_with_expression_collection(self):
        """Test ScatterArgs with expression string collection."""
        args = ScatterArgs(
            collection="${{ ACTIONS.previous.result }}",
            interval=1.0,
        )
        assert args.collection == "${{ ACTIONS.previous.result }}"
        assert args.interval == 1.0

    def test_scatter_args_interval_float(self):
        """Test ScatterArgs interval accepts various float values."""
        test_intervals = [0.1, 0.5, 1.0, 2.5, 10.0]
        for interval in test_intervals:
            args = ScatterArgs(collection=[], interval=interval)
            assert args.interval == interval

    def test_scatter_args_model_dump(self):
        """Test ScatterArgs.model_dump() includes interval."""
        args = ScatterArgs(collection=[1, 2, 3], interval=0.5)
        dumped = args.model_dump()
        assert "collection" in dumped
        assert "interval" in dumped
        assert dumped["interval"] == 0.5

    def test_scatter_args_model_dump_without_interval(self):
        """Test ScatterArgs.model_dump() with None interval."""
        args = ScatterArgs(collection=[1, 2, 3])
        dumped = args.model_dump()
        assert "collection" in dumped
        assert dumped["interval"] is None


class TestTaskEquality:
    """Test Task equality with delay field.

    Note: delay field is excluded from equality checks (compare=False)
    so tasks with different delays are still considered equal.
    """

    def test_tasks_equal_without_delay(self):
        """Test two tasks are equal when both have no delay."""
        stream_id = StreamID("root:0")
        task1 = Task(ref="test", stream_id=stream_id)
        task2 = Task(ref="test", stream_id=stream_id)
        assert task1 == task2

    def test_tasks_equal_with_same_delay(self):
        """Test two tasks are equal when they have the same delay."""
        stream_id = StreamID("root:0")
        task1 = Task(ref="test", stream_id=stream_id, delay=1.0)
        task2 = Task(ref="test", stream_id=stream_id, delay=1.0)
        assert task1 == task2

    def test_tasks_equal_different_delay(self):
        """Test two tasks are equal even with different delays (delay not compared)."""
        stream_id = StreamID("root:0")
        task1 = Task(ref="test", stream_id=stream_id, delay=1.0)
        task2 = Task(ref="test", stream_id=stream_id, delay=2.0)
        assert task1 == task2  # Equal because delay is not compared

    def test_tasks_equal_one_with_delay(self):
        """Test tasks are equal even when only one has a delay (delay not compared)."""
        stream_id = StreamID("root:0")
        task1 = Task(ref="test", stream_id=stream_id, delay=1.0)
        task2 = Task(ref="test", stream_id=stream_id)
        assert task1 == task2  # Equal because delay is not compared

    def test_tasks_hashable_with_delay(self):
        """Test tasks with delays can be used as dict keys."""
        stream_id = StreamID("root:0")
        task1 = Task(ref="test", stream_id=stream_id, delay=1.0)
        task2 = Task(ref="test", stream_id=stream_id, delay=2.0)

        # Both should hash to the same value
        assert hash(task1) == hash(task2)

        # Can use as dict keys
        task_dict = {task1: "value1"}
        assert task_dict[task2] == "value1"  # task2 should find task1's value
