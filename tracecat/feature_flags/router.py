from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from tracecat.config import TRACECAT__FEATURE_FLAGS
from tracecat.feature_flags.enums import FeatureFlag

router = APIRouter(prefix="/feature-flags", tags=["feature-flags"])


class FeatureFlagsRead(BaseModel):
    """Response model for feature flags."""

    enabled_features: list[FeatureFlag]


@router.get("", response_model=FeatureFlagsRead)
async def get_feature_flags() -> FeatureFlagsRead:
    """Get the list of enabled feature flags.

    This endpoint is public and doesn't require authentication,
    as feature flags are not sensitive information.
    """
    return FeatureFlagsRead(enabled_features=sorted(TRACECAT__FEATURE_FLAGS))
