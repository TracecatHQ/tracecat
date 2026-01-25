"""Admin API router - platform-level management."""

from fastapi import APIRouter

from tracecat_ee.admin.organizations.router import router as organizations_router
from tracecat_ee.admin.registry.router import router as registry_router
from tracecat_ee.admin.settings.router import router as settings_router
from tracecat_ee.admin.tiers.router import router as tiers_router
from tracecat_ee.admin.users.router import router as users_router

router = APIRouter(prefix="/admin", tags=["admin"])

router.include_router(organizations_router)
router.include_router(registry_router)
router.include_router(settings_router)
router.include_router(tiers_router)
router.include_router(users_router)
