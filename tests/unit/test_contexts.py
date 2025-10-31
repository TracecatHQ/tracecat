from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat.contexts import ctx_session, with_session


class TestSessionContext:
    """Test cases for the session context manager."""

    @pytest.mark.anyio
    async def test_with_session_sets_and_resets_context(self):
        """Test that with_session properly sets and resets the context."""
        # Verify context is initially None
        assert ctx_session.get(None) is None

        # Create a mock session
        mock_session = AsyncMock(spec=AsyncSession)

        # Use the context manager
        async with with_session(mock_session):
            # Verify the session is set in context
            assert ctx_session.get() is mock_session

        # Verify context is reset after exiting
        assert ctx_session.get(None) is None

    @pytest.mark.anyio
    async def test_nested_async_functions_access_same_session(self):
        """Test that nested async functions can access the same session from context."""
        mock_session = AsyncMock(spec=AsyncSession)

        async def inner_async_function() -> AsyncSession | None:
            """Inner async function that should access the same session."""
            return ctx_session.get(None)

        async def another_inner_function() -> AsyncSession | None:
            """Another inner async function."""
            return ctx_session.get(None)

        # Verify context is initially None
        assert await inner_async_function() is None
        assert await another_inner_function() is None

        async with with_session(mock_session):
            # All nested functions should get the same session
            inner_session = await inner_async_function()
            another_session = await another_inner_function()
            direct_session = ctx_session.get()

            assert inner_session is mock_session
            assert another_session is mock_session
            assert direct_session is mock_session
            assert inner_session is another_session is direct_session

        # Verify context is reset after exiting
        assert await inner_async_function() is None
        assert await another_inner_function() is None

    @pytest.mark.anyio
    async def test_multiline_context_manager_works(self):
        """Test that the context manager works correctly with multiline usage patterns."""
        mock_session1 = AsyncMock(spec=AsyncSession)
        mock_session2 = AsyncMock(spec=AsyncSession)

        # Test multiline context manager usage
        async with with_session(mock_session1):
            # Should get the first session
            assert ctx_session.get() is mock_session1

            async def nested_function():
                return ctx_session.get()

            assert await nested_function() is mock_session1

        # Context should be reset
        assert ctx_session.get(None) is None

        # Test with multiple context managers in parentheses
        async with with_session(mock_session2):
            # Should get the second session
            assert ctx_session.get() is mock_session2

        # Context should be reset again
        assert ctx_session.get(None) is None

    @pytest.mark.anyio
    async def test_context_isolation_between_sessions(self):
        """Test that different sessions don't interfere with each other."""
        mock_session1 = AsyncMock(spec=AsyncSession)
        mock_session2 = AsyncMock(spec=AsyncSession)

        # First session context
        async with with_session(mock_session1):
            assert ctx_session.get() is mock_session1

            # Nested session context should override
            async with with_session(mock_session2):
                assert ctx_session.get() is mock_session2

            # Should restore to first session
            assert ctx_session.get() is mock_session1

        # Should be reset to None
        assert ctx_session.get(None) is None

    @pytest.mark.anyio
    async def test_exception_handling_resets_context(self):
        """Test that exceptions in the context manager properly reset the context."""
        mock_session = AsyncMock(spec=AsyncSession)

        # Verify context starts as None
        assert ctx_session.get(None) is None

        try:
            async with with_session(mock_session):
                # Verify session is set
                assert ctx_session.get() is mock_session
                # Raise an exception
                raise ValueError("Test exception")
        except ValueError:
            pass

        # Context should still be reset even after exception
        assert ctx_session.get(None) is None

    @pytest.mark.anyio
    async def test_deeply_nested_async_calls_maintain_session(self):
        """Test that deeply nested async function calls maintain access to the same session."""
        mock_session = AsyncMock(spec=AsyncSession)

        async def level_1():
            return await level_2()

        async def level_2():
            return await level_3()

        async def level_3():
            return ctx_session.get(None)

        # Without session context
        assert await level_1() is None

        # With session context
        async with with_session(mock_session):
            result = await level_1()
            assert result is mock_session

        # After context
        assert await level_1() is None

    @pytest.mark.anyio
    async def test_simulate_real_world_usage_pattern(self):
        """Test a pattern that simulates how the context manager is used in the real codebase."""
        mock_session = AsyncMock(spec=AsyncSession)

        async def simulate_database_operation():
            """Simulate a database operation that uses the context session."""
            current_session = ctx_session.get(None)
            if current_session is None:
                raise ValueError("No session in context")
            return current_session

        async def nested_service_call():
            """Simulate a nested service call that also needs the session."""
            return await simulate_database_operation()

        # Test simulating real-world usage pattern
        async with with_session(mock_session):
            # Direct access should work
            context_session = ctx_session.get()
            assert context_session is mock_session

            # Simulated database operations should get the same session
            db_session = await simulate_database_operation()
            assert db_session is mock_session

            # Nested calls should also get the same session
            nested_session = await nested_service_call()
            assert nested_session is mock_session

            # All should be the same instance
            assert context_session is db_session is nested_session is mock_session

        # Context should be reset after exiting
        assert ctx_session.get(None) is None

        # Operations outside context should fail appropriately
        with pytest.raises(ValueError, match="No session in context"):
            await simulate_database_operation()

    @pytest.mark.anyio
    async def test_multiline_context_manager_pattern(self):
        """Test multiline context manager syntax commonly used in the codebase."""
        mock_session1 = AsyncMock(spec=AsyncSession)
        mock_session2 = AsyncMock(spec=AsyncSession)

        # Test the multiline context manager pattern with multiple mock sessions
        async with with_session(session=mock_session1):
            # The session should be available in context
            context_session = ctx_session.get()
            assert context_session is mock_session1

            async def inner_operation():
                return ctx_session.get()

            # Inner functions should also get the same session
            inner_session = await inner_operation()
            assert inner_session is mock_session1 is context_session

        # Context should be properly reset
        assert ctx_session.get(None) is None

        # Test with another session to ensure isolation
        async with with_session(session=mock_session2):
            context_session = ctx_session.get()
            assert context_session is mock_session2
            assert context_session is not mock_session1

        assert ctx_session.get(None) is None

    @pytest.mark.anyio
    async def test_multiple_context_managers_together(self):
        """Test using multiple context managers in the same async with statement."""

        mock_session = AsyncMock(spec=AsyncSession)
        mock_resource = AsyncMock()

        @asynccontextmanager
        async def mock_resource_manager():
            try:
                yield mock_resource
            finally:
                pass

        # Test multiple context managers in same statement
        async with (
            mock_resource_manager() as resource,
            with_session(session=mock_session),
        ):
            # Both context managers should be active
            assert resource is mock_resource
            assert ctx_session.get() is mock_session

            async def nested_check():
                # Nested function should access the session
                return ctx_session.get()

            nested_session = await nested_check()
            assert nested_session is mock_session

        # Session context should be reset
        assert ctx_session.get(None) is None

    @pytest.mark.anyio
    async def test_multiple_context_managers_with_database_pattern(self):
        """Test the common pattern of using session manager with session context."""

        mock_db_session = AsyncMock(spec=AsyncSession)

        @asynccontextmanager
        async def mock_session_manager():
            """Mock the get_async_session_context_manager pattern."""
            try:
                yield mock_db_session
            finally:
                await mock_db_session.close()

        # Test the pattern commonly used in the codebase
        async with (
            mock_session_manager() as db_session,
            with_session(session=db_session),
        ):
            # The session from the manager should be in context
            context_session = ctx_session.get()
            assert context_session is db_session is mock_db_session

            async def service_operation():
                # Service operations should access the same session
                return ctx_session.get()

            service_session = await service_operation()
            assert service_session is mock_db_session

        # Context should be cleaned up
        assert ctx_session.get(None) is None

    @pytest.mark.anyio
    async def test_multiple_nested_context_managers(self):
        """Test multiple context managers with different nesting levels."""

        session1 = AsyncMock(spec=AsyncSession)
        session2 = AsyncMock(spec=AsyncSession)

        @asynccontextmanager
        async def other_manager():
            yield "other_resource"

        # Test nested multiple context managers
        async with (
            other_manager() as resource1,
            with_session(session=session1),
        ):
            assert resource1 == "other_resource"
            assert ctx_session.get() is session1

            # Nest another set of multiple context managers
            async with (
                other_manager() as resource2,
                with_session(session=session2),
            ):
                assert resource2 == "other_resource"
                assert ctx_session.get() is session2  # Should override with session2

                async def deep_nested():
                    return ctx_session.get()

                assert await deep_nested() is session2

            # Should restore to session1
            assert ctx_session.get() is session1

        # Should be reset to None
        assert ctx_session.get(None) is None
