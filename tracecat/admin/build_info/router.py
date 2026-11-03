"""Superuser-only platform build details."""

from datetime import datetime

from fastapi import APIRouter

from tracecat import __version__ as APP_VERSION
from tracecat import config
from tracecat.admin.build_info.schemas import BuildEdition, PlatformBuildInfo
from tracecat.auth.credentials import SuperuserRole
from tracecat.tiers.defaults import has_installed_agent_backend

router = APIRouter(prefix="/build-info", tags=["admin:build"])


def _parse_build_time(value: str | None) -> datetime | None:
    """Parse the baked-in build time, ignoring values that are not ISO 8601."""
    if value is None:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


@router.get("", response_model=PlatformBuildInfo)
async def get_build_info(role: SuperuserRole) -> PlatformBuildInfo:
    """Return the version and build details of the running deployment."""
    edition = BuildEdition.EE if has_installed_agent_backend() else BuildEdition.OSS
    return PlatformBuildInfo(
        version=APP_VERSION,
        edition=edition,
        commit=config.TRACECAT__BUILD_COMMIT,
        built_at=_parse_build_time(config.TRACECAT__BUILD_TIME),
    )
