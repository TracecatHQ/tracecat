import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import SecretStr
from tracecat_registry import (
    RegistryOAuthSecret,
    RegistrySecret,
    RegistrySecretType,
)

from tracecat.dsl.models import ActionStatement, RunActionInput, RunContext
from tracecat.executor.models import DispatchActionContext
from tracecat.executor.service import (
    _dispatch_action,
    dispatch_action_on_cluster,
    get_action_secrets,
    run_action_from_input,
)
from tracecat.expressions.common import ExprContext
from tracecat.git.models import GitUrl
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.integrations.enums import OAuthGrantType
from tracecat.registry.actions.service import RegistryActionsService
from tracecat.types.auth import Role
from tracecat.types.exceptions import TracecatCredentialsError


@pytest.fixture
def mock_session():
    return AsyncMock()


@pytest.fixture
def basic_task_input():
    """Fixture that provides a basic RunActionInput without looping."""
    wf_id = WorkflowUUID.new_uuid4()
    wf_exec_id = wf_id.short() + "/exec_test"
    wf_run_id = uuid.uuid4()
    return RunActionInput(
        task=ActionStatement(
            action="test_action",
            args={"key": "value"},
            ref="test_ref",
        ),
        exec_context={
            ExprContext.ACTIONS: {
                "test_action": {
                    "args": {"key": "value"},
                    "ref": "test-ref",
                }
            }
        },
        run_context=RunContext(
            wf_id=wf_id,
            wf_exec_id=wf_exec_id,
            wf_run_id=wf_run_id,
            environment="test-env",
        ),
    )


@pytest.fixture
def basic_looped_task_input():
    wf_id = WorkflowUUID.new_uuid4()
    wf_exec_id = wf_id.short() + "/exec_test"
    wf_run_id = uuid.uuid4()
    return RunActionInput(
        task=ActionStatement(
            action="test_action",
            args={"key": "value"},
            ref="test_ref",
            for_each="${{ for var.x in [1,2,3] }}",
        ),
        exec_context={
            ExprContext.ACTIONS: {
                "test_action": {
                    "args": {"key": "value"},
                    "ref": "test-ref",
                }
            }
        },
        run_context=RunContext(
            wf_id=wf_id,
            wf_exec_id=wf_exec_id,
            wf_run_id=wf_run_id,
            environment="test-env",
        ),
    )


@pytest.fixture
def dispatch_context():
    return DispatchActionContext(
        role=Role(type="service", service_id="tracecat-executor"),
        ssh_command="ssh -i /tmp/key",
        git_url=GitUrl(host="github.com", org="org", repo="repo", ref="abc123"),
    )


@pytest.mark.anyio
async def test_dispatch_action_basic(mock_session, basic_task_input, dispatch_context):
    with patch("tracecat.executor.service.run_action_on_ray_cluster") as mock_ray:
        mock_ray.return_value = {"result": "success"}

        result = await _dispatch_action(input=basic_task_input, ctx=dispatch_context)

        assert result == {"result": "success"}
        mock_ray.assert_called_once_with(basic_task_input, dispatch_context)


@pytest.mark.anyio
async def test_dispatch_action_with_foreach(
    mock_session, basic_looped_task_input, dispatch_context
):
    with patch("tracecat.executor.service.run_action_on_ray_cluster") as mock_ray:
        mock_ray.return_value = {"result": "success"}

        result = await _dispatch_action(
            input=basic_looped_task_input, ctx=dispatch_context
        )

        assert result == [{"result": "success"}] * 3

        # Assert the number of calls
        assert mock_ray.call_count == 3

        # Get all calls and their arguments
        calls = mock_ray.call_args_list

        # Verify each call's arguments
        for i, call in enumerate(calls, 1):
            args, kwargs = call
            input_arg = args[0]
            # Verify the loop variable 'x' was set to different values (1, 2, 3)
            assert input_arg.task.args["key"] == "value"
            assert input_arg.exec_context[ExprContext.LOCAL_VARS] == {"x": i}
            assert args[1] == dispatch_context


@pytest.mark.anyio
async def test_dispatch_action_with_git_url(mock_session, basic_task_input):
    """Test dispatch_action_on_cluster with a git url, patching get_async_session_context_manager."""
    # Import ctx_role to set the role context
    from tracecat.contexts import ctx_role

    # Set up the role context for the test
    test_role = Role(type="service", service_id="tracecat-executor")
    ctx_role.set(test_role)

    # Patch the async session context manager to yield a mock session
    @asynccontextmanager
    async def mock_session_cm():
        yield mock_session

    with (
        patch(
            "tracecat.executor.service.get_async_session_context_manager",
            return_value=mock_session_cm(),
        ),
        patch("tracecat.executor.service.safe_prepare_git_url") as mock_git_url,
        patch("tracecat.executor.service._dispatch_action") as mock_dispatch,
        patch("tracecat.executor.service.get_ssh_command") as mock_ssh_cmd,
    ):
        mock_git_url.return_value = GitUrl(
            host="github.com", org="org", repo="repo", ref="abc123"
        )
        mock_ssh_cmd.return_value = "ssh -i /tmp/key"
        mock_dispatch.return_value = {"result": "success"}

        result = await dispatch_action_on_cluster(input=basic_task_input)

        assert result == {"result": "success"}
        mock_git_url.assert_called_once()
        mock_ssh_cmd.assert_called_once()


@pytest.mark.anyio
async def test_run_action_from_input_secrets_handling(mocker, test_role):
    """Test that run_action_from_input correctly handles secrets as sets without converting to lists."""
    # Mock the registry action service
    mock_reg_service = mocker.AsyncMock(spec=RegistryActionsService)
    mock_reg_service.get_action.return_value = mocker.MagicMock()
    mock_reg_service.get_bound.return_value = mocker.MagicMock()

    # Create some registry secrets
    registry_secrets = [
        RegistrySecret(name="required_secret1", keys=["REQ_KEY1"], optional=False),
        RegistrySecret(name="required_secret2", keys=["REQ_KEY2"], optional=False),
        RegistrySecret(name="optional_secret1", keys=["OPT_KEY1"], optional=True),
        RegistrySecret(name="optional_secret2", keys=["OPT_KEY2"], optional=True),
    ]
    mock_reg_service.fetch_all_action_secrets.return_value = registry_secrets

    # Mock the with_session context manager
    mocker.patch(
        "tracecat.registry.actions.service.RegistryActionsService.with_session",
        return_value=mocker.AsyncMock(
            __aenter__=mocker.AsyncMock(return_value=mock_reg_service)
        ),
    )

    # Mock the task args with templated secrets
    task_args = {
        "param1": "{{ secrets.args_secret1 }}",
        "param2": {"nested": "{{ secrets.args_secret2 }}"},
    }

    # Create a run action input
    wf_id = WorkflowUUID.new_uuid4()
    wf_exec_id = wf_id.short() + "/exec_test"
    wf_run_id = uuid.uuid4()

    task = ActionStatement(ref="test_task", action="test_action", args=task_args)
    input = RunActionInput(
        task=task,
        exec_context={},
        run_context=RunContext(
            wf_id=wf_id,
            wf_exec_id=wf_exec_id,
            wf_run_id=wf_run_id,
            environment="test-env",
        ),
    )

    # Mock extract_templated_secrets to return some args secrets
    mocker.patch(
        "tracecat.executor.service.extract_templated_secrets",
        return_value=["args_secret1", "args_secret2"],
    )

    # Mock get_runtime_env
    mocker.patch("tracecat.executor.service.get_runtime_env", return_value="test_env")

    # Mock the AuthSandbox
    mock_sandbox = mocker.MagicMock()
    mock_sandbox.secrets = {}
    mock_sandbox.__aenter__.return_value = mock_sandbox
    mock_sandbox.__aexit__.return_value = None

    auth_sandbox_mock = mocker.patch("tracecat.executor.service.AuthSandbox")
    auth_sandbox_mock.return_value = mock_sandbox

    # Mock run_single_action to avoid actually running the action
    mocker.patch("tracecat.executor.service.run_single_action", return_value="result")

    # Mock evaluate_templated_args
    mocker.patch("tracecat.executor.service.evaluate_templated_args", return_value={})

    # Mock env_sandbox
    mocker.patch("tracecat.executor.service.env_sandbox")

    # Mock flatten_secrets
    mocker.patch("tracecat.executor.service.flatten_secrets", return_value={})

    # Run the function
    await run_action_from_input(input, test_role)

    # Check that AuthSandbox was called with sets, not lists
    auth_sandbox_mock.assert_called_once()
    call_args, call_kwargs = auth_sandbox_mock.call_args

    # Verify that secrets parameter is a set
    assert isinstance(call_kwargs["secrets"], set)
    expected_secrets = {
        "required_secret1",
        "required_secret2",
        "optional_secret1",
        "optional_secret2",
        "args_secret1",
        "args_secret2",
    }
    assert call_kwargs["secrets"] == expected_secrets

    # Verify that optional_secrets parameter is a set
    assert isinstance(call_kwargs["optional_secrets"], set)
    expected_optional_secrets = {"optional_secret1", "optional_secret2"}
    assert call_kwargs["optional_secrets"] == expected_optional_secrets

    # Verify environment parameter
    assert call_kwargs["environment"] == "test_env"


@pytest.mark.anyio
async def test_get_action_secrets_skips_optional_oauth(mocker):
    """Ensure optional OAuth integrations do not raise when missing."""

    action_secrets: set[RegistrySecretType] = {
        RegistryOAuthSecret(
            provider_id="azure_log_analytics",
            grant_type="authorization_code",
        ),
        RegistryOAuthSecret(
            provider_id="azure_log_analytics",
            grant_type="client_credentials",
            optional=True,
        ),
    }

    mocker.patch("tracecat.executor.service.extract_templated_secrets", return_value=[])
    mocker.patch("tracecat.executor.service.get_runtime_env", return_value="test_env")

    sandbox = mocker.AsyncMock()
    sandbox.secrets = {}
    sandbox.__aenter__.return_value = sandbox
    sandbox.__aexit__.return_value = None
    mocker.patch("tracecat.executor.service.AuthSandbox", return_value=sandbox)

    delegated_integration = mocker.MagicMock()
    delegated_integration.provider_id = "azure_log_analytics"
    delegated_integration.grant_type = OAuthGrantType.AUTHORIZATION_CODE

    service = mocker.AsyncMock()
    service.list_integrations.return_value = [delegated_integration]
    service.refresh_token_if_needed.return_value = delegated_integration
    service.get_access_token.return_value = SecretStr("user-token")

    @asynccontextmanager
    async def service_cm():
        yield service

    mocker.patch(
        "tracecat.executor.service.IntegrationService.with_session",
        return_value=service_cm(),
    )

    secrets = await get_action_secrets({}, action_secrets)
    assert (
        secrets["azure_log_analytics"]["AZURE_LOG_ANALYTICS_USER_TOKEN"] == "user-token"
    )
    assert "AZURE_LOG_ANALYTICS_SERVICE_TOKEN" not in secrets["azure_log_analytics"]


@pytest.mark.anyio
async def test_get_action_secrets_merges_multiple_oauth_tokens(mocker):
    """Ensure both delegated and service tokens are returned when available."""

    action_secrets: set[RegistrySecretType] = {
        RegistryOAuthSecret(
            provider_id="azure_log_analytics",
            grant_type="authorization_code",
        ),
        RegistryOAuthSecret(
            provider_id="azure_log_analytics",
            grant_type="client_credentials",
            optional=True,
        ),
    }

    mocker.patch("tracecat.executor.service.extract_templated_secrets", return_value=[])
    mocker.patch("tracecat.executor.service.get_runtime_env", return_value="test_env")

    sandbox = mocker.AsyncMock()
    sandbox.secrets = {}
    sandbox.__aenter__.return_value = sandbox
    sandbox.__aexit__.return_value = None
    mocker.patch("tracecat.executor.service.AuthSandbox", return_value=sandbox)

    delegated_integration = mocker.MagicMock()
    delegated_integration.provider_id = "azure_log_analytics"
    delegated_integration.grant_type = OAuthGrantType.AUTHORIZATION_CODE

    service_integration = mocker.MagicMock()
    service_integration.provider_id = "azure_log_analytics"
    service_integration.grant_type = OAuthGrantType.CLIENT_CREDENTIALS

    service = mocker.AsyncMock()
    service.list_integrations.return_value = [
        delegated_integration,
        service_integration,
    ]
    service.refresh_token_if_needed.side_effect = lambda integration: integration

    def _get_access_token(integration):
        if integration.grant_type == OAuthGrantType.AUTHORIZATION_CODE:
            return SecretStr("user-token")
        if integration.grant_type == OAuthGrantType.CLIENT_CREDENTIALS:
            return SecretStr("service-token")
        return None

    service.get_access_token.side_effect = _get_access_token

    @asynccontextmanager
    async def service_cm():
        yield service

    mocker.patch(
        "tracecat.executor.service.IntegrationService.with_session",
        return_value=service_cm(),
    )

    secrets = await get_action_secrets({}, action_secrets)
    assert (
        secrets["azure_log_analytics"]["AZURE_LOG_ANALYTICS_USER_TOKEN"] == "user-token"
    )
    assert (
        secrets["azure_log_analytics"]["AZURE_LOG_ANALYTICS_SERVICE_TOKEN"]
        == "service-token"
    )


@pytest.mark.anyio
async def test_get_action_secrets_missing_required_oauth_raises(mocker):
    """Required OAuth integrations should surface a credentials error."""

    action_secrets: set[RegistrySecretType] = {
        RegistryOAuthSecret(
            provider_id="azure_log_analytics",
            grant_type="authorization_code",
        )
    }

    mocker.patch("tracecat.executor.service.extract_templated_secrets", return_value=[])
    mocker.patch("tracecat.executor.service.get_runtime_env", return_value="test_env")

    sandbox = mocker.AsyncMock()
    sandbox.secrets = {}
    sandbox.__aenter__.return_value = sandbox
    sandbox.__aexit__.return_value = None
    mocker.patch("tracecat.executor.service.AuthSandbox", return_value=sandbox)

    service = mocker.AsyncMock()
    service.list_integrations.return_value = []

    @asynccontextmanager
    async def service_cm():
        yield service

    mocker.patch(
        "tracecat.executor.service.IntegrationService.with_session",
        return_value=service_cm(),
    )

    with pytest.raises(TracecatCredentialsError):
        await get_action_secrets({}, action_secrets)


@pytest.mark.anyio
async def test_extract_templated_secrets_detects_nested_complex_expressions():
    from tracecat.expressions.eval import extract_templated_secrets

    expr = '${{ FN.to_base64(SECRETS.zendesk.ZENDESK_EMAIL + "/token:" + SECRETS.zendesk.ZENDESK_API_TOKEN) }}'
    secrets = extract_templated_secrets(expr)
    assert sorted(secrets) == sorted(
        [
            "zendesk.ZENDESK_EMAIL",
            "zendesk.ZENDESK_API_TOKEN",
        ]
    )


@pytest.mark.anyio
async def test_git_context_cache_hit(mock_session):
    """Test that git context is cached and reused on subsequent calls."""
    from tracecat.contexts import ctx_role
    from tracecat.executor.service import _git_context_cache, get_git_context_cached

    # Clear the cache first
    _git_context_cache.clear()

    # Set up the role context
    test_role = Role(
        type="service",
        service_id="tracecat-executor",
        workspace_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
    )
    ctx_role.set(test_role)

    # Mock the session context manager
    @asynccontextmanager
    async def mock_session_cm():
        yield mock_session

    def get_session_cm():
        return mock_session_cm()

    with (
        patch(
            "tracecat.executor.service.get_async_session_context_manager",
            new=get_session_cm,
        ),
        patch("tracecat.executor.service.safe_prepare_git_url") as mock_git_url,
        patch("tracecat.executor.service.get_ssh_command") as mock_ssh_cmd,
    ):
        # Set up mock returns
        test_git_url = GitUrl(host="github.com", org="test", repo="repo", ref="abc123")
        mock_git_url.return_value = test_git_url
        mock_ssh_cmd.return_value = "ssh -i /tmp/test_key"

        # First call - should fetch from DB
        git_url1, ssh_cmd1 = await get_git_context_cached(role=test_role)
        assert git_url1 == test_git_url
        assert ssh_cmd1 == "ssh -i /tmp/test_key"
        assert mock_git_url.call_count == 1
        assert mock_ssh_cmd.call_count == 1

        # Second call - should use cache
        git_url2, ssh_cmd2 = await get_git_context_cached(role=test_role)
        assert git_url2 == test_git_url
        assert ssh_cmd2 == "ssh -i /tmp/test_key"
        # Should still be 1 since it used cache
        assert mock_git_url.call_count == 1
        assert mock_ssh_cmd.call_count == 1

        # Third call - verify cache still works
        git_url3, ssh_cmd3 = await get_git_context_cached(role=test_role)
        assert git_url3 == test_git_url
        assert ssh_cmd3 == "ssh -i /tmp/test_key"
        assert mock_git_url.call_count == 1
        assert mock_ssh_cmd.call_count == 1


@pytest.mark.anyio
async def test_git_context_cache_expiry(mock_session):
    """Test that git context cache expires after TTL and refetches."""
    from tracecat.contexts import ctx_role
    from tracecat.executor.service import _git_context_cache, get_git_context_cached

    # Clear the cache first
    _git_context_cache.clear()

    # Set up the role context
    test_role = Role(
        type="service",
        service_id="tracecat-executor",
        workspace_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
    )
    ctx_role.set(test_role)

    # Mock the session context manager
    @asynccontextmanager
    async def mock_session_cm():
        yield mock_session

    def get_session_cm():
        return mock_session_cm()

    with (
        patch(
            "tracecat.executor.service.get_async_session_context_manager",
            new=get_session_cm,
        ),
        patch("tracecat.executor.service.safe_prepare_git_url") as mock_git_url,
        patch("tracecat.executor.service.get_ssh_command") as mock_ssh_cmd,
        patch("tracecat.executor.service.time.time") as mock_time,
    ):
        # Set up mock returns
        test_git_url = GitUrl(host="github.com", org="test", repo="repo", ref="def456")
        mock_git_url.return_value = test_git_url
        mock_ssh_cmd.return_value = "ssh -i /tmp/expired_key"

        # Set initial time
        initial_time = 1000.0
        mock_time.return_value = initial_time

        # First call - should fetch from DB
        git_url1, ssh_cmd1 = await get_git_context_cached(role=test_role)
        assert git_url1 == test_git_url
        assert ssh_cmd1 == "ssh -i /tmp/expired_key"
        assert mock_git_url.call_count == 1

        # Advance time by 40 seconds (still within 60 second TTL)
        mock_time.return_value = initial_time + 40

        # Second call - should use cache
        git_url2, ssh_cmd2 = await get_git_context_cached(role=test_role)
        assert git_url2 == test_git_url
        assert ssh_cmd2 == "ssh -i /tmp/expired_key"
        assert mock_git_url.call_count == 1  # Still 1

        # Advance time by 61 seconds (beyond 60 second TTL)
        mock_time.return_value = initial_time + 61

        # Update mock returns for new fetch
        new_git_url = GitUrl(host="github.com", org="test", repo="repo", ref="ghi789")
        mock_git_url.return_value = new_git_url
        mock_ssh_cmd.return_value = "ssh -i /tmp/new_key"

        # Third call - should refetch due to expired cache
        git_url3, ssh_cmd3 = await get_git_context_cached(role=test_role)
        assert git_url3 == new_git_url
        assert ssh_cmd3 == "ssh -i /tmp/new_key"
        assert mock_git_url.call_count == 2  # Should have fetched again


@pytest.mark.anyio
async def test_git_context_cache_different_workspaces(mock_session):
    """Test that different workspaces have separate cache entries."""
    from tracecat.contexts import ctx_role
    from tracecat.executor.service import _git_context_cache, get_git_context_cached

    # Clear the cache first
    _git_context_cache.clear()

    # Set up two different roles with different workspaces
    ws1_id = uuid.uuid4()
    ws2_id = uuid.uuid4()
    role1 = Role(
        type="service",
        service_id="tracecat-executor",
        workspace_id=ws1_id,
        user_id=uuid.uuid4(),
    )
    role2 = Role(
        type="service",
        service_id="tracecat-executor",
        workspace_id=ws2_id,
        user_id=uuid.uuid4(),
    )

    # Mock the session context manager
    @asynccontextmanager
    async def mock_session_cm():
        yield mock_session

    def get_session_cm():
        return mock_session_cm()

    with (
        patch(
            "tracecat.executor.service.get_async_session_context_manager",
            new=get_session_cm,
        ),
        patch("tracecat.executor.service.safe_prepare_git_url") as mock_git_url,
        patch("tracecat.executor.service.get_ssh_command") as mock_ssh_cmd,
    ):
        # Set up mock to return different values based on role
        git_url1 = GitUrl(host="github.com", org="org1", repo="repo1", ref="ref1")
        git_url2 = GitUrl(host="github.com", org="org2", repo="repo2", ref="ref2")

        def git_url_side_effect(*args, **kwargs):
            role = kwargs.get("role")
            if role and role.workspace_id == ws1_id:
                return git_url1
            return git_url2

        def ssh_cmd_side_effect(*args, **kwargs):
            role = kwargs.get("role")
            if role and role.workspace_id == ws1_id:
                return "ssh -i /tmp/key1"
            return "ssh -i /tmp/key2"

        mock_git_url.side_effect = git_url_side_effect
        mock_ssh_cmd.side_effect = ssh_cmd_side_effect

        # Fetch for role1
        ctx_role.set(role1)
        result1 = await get_git_context_cached(role=role1)
        assert result1[0] == git_url1
        assert result1[1] == "ssh -i /tmp/key1"
        assert mock_git_url.call_count == 1

        # Fetch for role2 - should not use role1's cache
        ctx_role.set(role2)
        result2 = await get_git_context_cached(role=role2)
        assert result2[0] == git_url2
        assert result2[1] == "ssh -i /tmp/key2"
        assert mock_git_url.call_count == 2  # Should have fetched for role2

        # Fetch again for role1 - should use cache
        ctx_role.set(role1)
        result1_cached = await get_git_context_cached(role=role1)
        assert result1_cached[0] == git_url1
        assert result1_cached[1] == "ssh -i /tmp/key1"
        assert mock_git_url.call_count == 2  # Still 2, used cache

        # Verify cache has entries for both workspaces
        assert len(_git_context_cache) == 2


@pytest.mark.anyio
async def test_git_context_cache_no_git_url(mock_session):
    """Test caching when no git URL is configured."""
    from tracecat.contexts import ctx_role
    from tracecat.executor.service import _git_context_cache, get_git_context_cached

    # Clear the cache first
    _git_context_cache.clear()

    # Set up the role context
    test_role = Role(
        type="service",
        service_id="tracecat-executor",
        workspace_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
    )
    ctx_role.set(test_role)

    # Mock the session context manager
    @asynccontextmanager
    async def mock_session_cm():
        yield mock_session

    def get_session_cm():
        return mock_session_cm()

    with (
        patch(
            "tracecat.executor.service.get_async_session_context_manager",
            new=get_session_cm,
        ),
        patch("tracecat.executor.service.safe_prepare_git_url") as mock_git_url,
        patch("tracecat.executor.service.get_ssh_command") as mock_ssh_cmd,
    ):
        # Set up mock to return None (no git configured)
        mock_git_url.return_value = None

        # First call - should fetch and get None
        git_url1, ssh_cmd1 = await get_git_context_cached(role=test_role)
        assert git_url1 is None
        assert ssh_cmd1 is None
        assert mock_git_url.call_count == 1
        assert (
            mock_ssh_cmd.call_count == 0
        )  # Should not call get_ssh_command when no git_url

        # Second call - should use cache
        git_url2, ssh_cmd2 = await get_git_context_cached(role=test_role)
        assert git_url2 is None
        assert ssh_cmd2 is None
        assert mock_git_url.call_count == 1  # Still 1, used cache
        assert mock_ssh_cmd.call_count == 0
