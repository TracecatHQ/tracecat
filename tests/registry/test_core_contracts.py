import pytest
from tracecat_registry.base.core.contracts import ensure


@pytest.mark.parametrize(
    "lhs, rhs, condition, should_pass",
    [
        # Threat score comparisons
        (85, 75, ">", True),
        (60, 60, ">=", True),
        (30, 50, "<", True),
        # IP address checks
        ("192.168.1.1", ["192.168.1.1", "10.0.0.1"], "in", True),
        ("8.8.8.8", ["192.168.1.1", "10.0.0.1"], "not in", True),
        # Status code validation
        (200, 200, "==", True),
        (403, 200, "!=", True),
        # Alert severity matching
        ("critical", "critical", "==", True),
        ("high", "medium", "!=", True),
        # Malware hash verification
        ("e1234...abc", "e1234...abc", "is", True),
        ("abc123", ["abc123", "def456"], "in", True),
        # Domain blocklist check
        ("malicious.com", ["malicious.com", "evil.com"], "in", True),
        ("good.com", ["malicious.com", "evil.com"], "not in", True),
        # Process state validation
        (None, None, "is", True),  # Process not found
        ("running", ["running", "suspended"], "in", True),
        # Incident count thresholds
        (5, 3, ">", True),
        (10, 10, ">=", True),
        (2, 5, "<", True),
    ],
)
def test_ensure(lhs, rhs, condition, should_pass):
    if should_pass:
        assert ensure(lhs, rhs, condition) is True
    else:
        with pytest.raises(AssertionError):
            ensure(lhs, rhs, condition)


@pytest.mark.parametrize(
    "lhs, rhs, condition",
    [
        (1, 1, "invalid"),  # Invalid operator
        ("malware.exe", ["malware.exe"], "<"),  # Invalid comparison for lists
        ({"ip": "1.1.1.1"}, ["1.1.1.1"], "in"),  # Invalid type for membership
        (85, 75, "contains"),  # Unsupported operator
        ("suspicious.exe", None, ">="),  # Invalid comparison with None
        (["alert1", "alert2"], "alert1", "in"),  # Reversed in operator usage
    ],
)
def test_ensure_invalid_condition(lhs, rhs, condition):
    with pytest.raises(AssertionError):
        ensure(lhs, rhs, condition)
