from tracecat.dsl.common import NON_RETRYABLE_ERROR_TYPES, RETRY_POLICIES


def test_agent_turn_retry_policy() -> None:
    policy = RETRY_POLICIES["activity:agent_turn"]

    assert policy.maximum_attempts == 2
    assert policy.non_retryable_error_types is NON_RETRYABLE_ERROR_TYPES


def test_fail_fast_retry_policy_remains_single_attempt() -> None:
    assert RETRY_POLICIES["activity:fail_fast"].maximum_attempts == 1
