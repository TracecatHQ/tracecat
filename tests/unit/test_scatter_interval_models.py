from tracecat.dsl.schemas import StreamID
from tracecat.dsl.types import Task


def test_task_identity_ignores_delay() -> None:
    """Scheduler keys tasks by identity, so delay must not affect equality or hash."""
    stream_id = StreamID("root:0")
    delayed = Task(ref="test", stream_id=stream_id, delay=1.0)
    undelayed = Task(ref="test", stream_id=stream_id)

    assert delayed == undelayed
    assert hash(delayed) == hash(undelayed)
    assert {delayed: "value"}[undelayed] == "value"
