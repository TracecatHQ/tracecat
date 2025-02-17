"""Tests for validation functions in the expressions module."""

from collections import OrderedDict, defaultdict, deque
from collections.abc import Mapping
from typing import Any

import pytest

from tracecat.common import is_iterable


@pytest.mark.parametrize(
    "value, container_only, expected",
    [
        # Test basic types with container_only=True
        ([], True, True),  # List
        ((), True, True),  # Tuple
        (set(), True, True),  # Set
        ({1, 2, 3}, True, True),  # Non-empty set
        ([1, 2, 3], True, True),  # Non-empty list
        # Test string-like types with container_only=True
        ("hello", True, False),  # String
        (b"hello", True, False),  # Bytes
        # Test string-like types with container_only=False
        ("hello", False, True),  # String
        (b"hello", False, True),  # Bytes
        # Test mapping types (should always be False)
        ({}, True, False),  # Dict
        ({"a": 1}, True, False),  # Non-empty dict
        ({}, False, False),  # Dict with container_only=False
        # Test non-iterable types
        (42, True, False),  # Integer
        (3.14, True, False),  # Float
        (True, True, False),  # Boolean
        (None, True, False),  # None
        # Test alternative container types
        (deque([1, 2, 3]), True, True),  # deque
        (deque(), True, True),  # Empty deque
        # Test alternative mapping types (should always be False)
        (defaultdict(list), True, False),  # defaultdict
        (defaultdict(list, {"a": [1, 2]}), True, False),  # Non-empty defaultdict
        (OrderedDict(), True, False),  # OrderedDict
        (OrderedDict([("a", 1), ("b", 2)]), True, False),  # Non-empty OrderedDict
    ],
)
def test_is_iterable(value: Any, container_only: bool, expected: bool) -> None:
    """Test the is_iterable function with various input types and container_only settings.

    Args:
        value: The value to test for iterability
        container_only: Whether to exclude string-like types from being considered iterable
        expected: The expected result of the is_iterable function
    """
    assert is_iterable(value, container_only=container_only) == expected


def test_is_iterable_custom_iterable() -> None:
    """Test is_iterable with a custom class implementing __iter__."""

    class CustomIterable:
        def __iter__(self):
            return iter([1, 2, 3])

    assert is_iterable(CustomIterable(), container_only=True)


def test_is_iterable_custom_mapping() -> None:
    """Test is_iterable with a custom mapping class."""

    class CustomMapping(Mapping):
        def __init__(self) -> None:
            self._data: dict[str, int] = {"a": 1}

        def __getitem__(self, key: str) -> int:
            return self._data[key]

        def __iter__(self):
            return iter(self._data)

        def __len__(self) -> int:
            return len(self._data)

    assert not is_iterable(CustomMapping(), container_only=True)
