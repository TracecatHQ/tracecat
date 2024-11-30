"""XXX(security): Use this only for testing purposes."""

import textwrap
from importlib.machinery import ModuleSpec
from types import ModuleType

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from tracecat import config
from tracecat.auth.credentials import RoleACL
from tracecat.registry.repository import Repository
from tracecat.types.auth import AccessLevel, Role


class TestRegistryParams(BaseModel):
    version: str
    code: str
    module_name: str
    validate_keys: list[str] | None = None


router = APIRouter(
    prefix="/test-registry", tags=["test-registry"], include_in_schema=False
)


@router.post("")
async def register_test_module(
    *,
    role: Role = RoleACL(
        allow_user=True,
        allow_service=True,
        require_workspace="yes",
        min_access_level=AccessLevel.ADMIN,
    ),
    params: TestRegistryParams,
):
    """Use this only for testing purposes."""
    if config.TRACECAT__APP_ENV != "development":
        # Failsafe
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This endpoint is only available in development mode",
        )
    registry = Repository(params.version)

    test_module = ModuleType(params.module_name)

    # Create a module spec for the test module
    module_spec = ModuleSpec(params.module_name, None)
    test_module.__spec__ = module_spec
    code = textwrap.dedent(params.code)
    # XXX(security): This is a security risk. Do not run this in production.
    # We're using exec to load the module because that's the only way to control
    # the remote registryfrom pytest
    exec(code, test_module.__dict__)
    registry._register_udfs_from_module(test_module)
    for key in params.validate_keys or []:
        assert key in registry
