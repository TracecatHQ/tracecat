from collections.abc import Callable

from fastapi import HTTPException, status

from tracecat import config
from tracecat.logger import logger


def is_feature_enabled(flag: str) -> bool:
    """Check if a feature flag is enabled."""
    return flag in config.TRACECAT__FEATURE_FLAGS


def feature_flag_dep(flag: str) -> Callable[..., None]:
    """Check if a feature flag is enabled."""

    def _is_feature_enabled() -> None:
        if not is_feature_enabled(flag):
            logger.debug("Feature flag is not enabled", flag=flag)
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Feature not enabled"
            )
        logger.debug("Feature flag is enabled", flag=flag)

    return _is_feature_enabled
