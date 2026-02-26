"""Unit tests for LoopEndArgs validation."""

import pytest
from pydantic import ValidationError

from tracecat.dsl.constants import MAX_DO_WHILE_ITERATIONS
from tracecat.dsl.schemas import LoopEndArgs


class TestLoopEndArgsModel:
    """Test LoopEndArgs max_iterations validation."""

    def test_loop_end_args_default_max_iterations(self):
        """Loop end defaults max_iterations to 100."""
        args = LoopEndArgs(condition="${{ True }}")
        assert args.max_iterations == 100

    def test_loop_end_args_accepts_platform_max(self):
        """Loop end accepts max_iterations at the platform cap."""
        args = LoopEndArgs(
            condition="${{ True }}",
            max_iterations=MAX_DO_WHILE_ITERATIONS,
        )
        assert args.max_iterations == MAX_DO_WHILE_ITERATIONS

    def test_loop_end_args_rejects_over_platform_max(self):
        """Loop end rejects max_iterations over the platform cap."""
        with pytest.raises(ValidationError):
            LoopEndArgs(
                condition="${{ True }}",
                max_iterations=MAX_DO_WHILE_ITERATIONS + 1,
            )
