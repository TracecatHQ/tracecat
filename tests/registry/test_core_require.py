import pytest
from tracecat_registry.base.core.require import require


@pytest.mark.parametrize(
    "lhs, rhs, condition",
    [
        # Threat score comparisons
        (85, 75, ">"),
        (60, 60, ">="),
        (30, 50, "<"),
        # IP address checks
        ("192.168.1.1", ["192.168.1.1", "10.0.0.1"], "in"),
        ("8.8.8.8", ["192.168.1.1", "10.0.0.1"], "not in"),
        # Status code validation
        (200, 200, "=="),
        (403, 200, "!="),
        # Alert severity matching
        ("critical", "critical", "=="),
        ("high", "medium", "!="),
        # Malware hash verification
        ("e1234...abc", "e1234...abc", "is"),
        ("abc123", ["abc123", "def456"], "in"),
        # Domain blocklist check
        ("malicious.com", ["malicious.com", "evil.com"], "in"),
        ("good.com", ["malicious.com", "evil.com"], "not in"),
        # Process state validation
        (None, None, "is"),
        ("running", ["running", "suspended"], "in"),
        # Incident count thresholds
        (5, 3, ">"),
        (10, 10, ">="),
        (2, 5, "<"),
    ],
)
def test_require_pass(lhs, rhs, condition):
    assert require(lhs, rhs, condition) is True


@pytest.mark.parametrize(
    "lhs, rhs, condition",
    [
        # Threat score comparisons
        (75, 85, ">"),  # Lower score not greater than higher
        (50, 60, ">="),  # Lower score not greater/equal than higher
        (50, 30, "<"),  # Higher score not less than lower
        # IP address checks
        ("192.168.1.1", ["10.0.0.1", "172.16.0.1"], "in"),  # IP not in list
        (
            "192.168.1.1",
            ["192.168.1.1", "10.0.0.1"],
            "not in",
        ),  # IP in list when shouldn't be
        # Status code validation
        (404, 200, "=="),  # Different status codes
        (200, 200, "!="),  # Same status codes when should differ
        # Alert severity matching
        ("critical", "high", "=="),  # Different severities
        ("medium", "medium", "!="),  # Same severity when should differ
        # Malware hash verification
        ("e1234...abc", "f5678...def", "is"),  # Different hashes
        ("xyz789", ["abc123", "def456"], "in"),  # Hash not in list
        # Domain blocklist check
        ("safe.com", ["malicious.com", "evil.com"], "in"),  # Domain not in blocklist
        (
            "evil.com",
            ["malicious.com", "evil.com"],
            "not in",
        ),  # Domain in blocklist when shouldn't be
        # Process state validation
        ("running", None, "is"),  # Process running when should be None
        ("stopped", ["running", "suspended"], "in"),  # State not in valid states
        # Incident count thresholds
        (3, 5, ">"),  # Lower count not greater than higher
        (5, 10, ">="),  # Lower count not greater/equal than higher
        (5, 2, "<"),  # Higher count not less than lower
    ],
)
def test_require_fail(lhs, rhs, condition):
    with pytest.raises(AssertionError):
        require(lhs, rhs, condition)


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
def test_require(lhs, rhs, condition, should_pass):
    if should_pass:
        assert require(lhs, rhs, condition) is True
    else:
        with pytest.raises(AssertionError):
            require(lhs, rhs, condition)
