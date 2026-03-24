from tracecat.agent.litellm_observability import LiteLLMLoadTracker


def test_load_tracker_tracks_connections_and_requests() -> None:
    tracker = LiteLLMLoadTracker()

    snapshot = tracker.begin_connection()
    assert snapshot.active_connections == 1
    assert snapshot.peak_active_connections == 1

    request_id, snapshot = tracker.begin_request()
    assert request_id == 1
    assert snapshot.active_requests == 1
    assert snapshot.total_requests == 1
    assert snapshot.peak_active_requests == 1

    snapshot = tracker.end_request()
    assert snapshot.active_requests == 0

    snapshot = tracker.end_connection()
    assert snapshot.active_connections == 0
