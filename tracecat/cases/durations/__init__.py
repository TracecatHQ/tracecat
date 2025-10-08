"""Case duration metric models and services."""

from .models import (
    CaseDurationAnchorSelection,
    CaseDurationComputation,
    CaseDurationCreate,
    CaseDurationEventAnchor,
    CaseDurationRead,
    CaseDurationUpdate,
)

__all__ = [
    "CaseDurationComputation",
    "CaseDurationCreate",
    "CaseDurationEventAnchor",
    "CaseDurationAnchorSelection",
    "CaseDurationRead",
    "CaseDurationService",
    "CaseDurationUpdate",
]


def __getattr__(name: str):
    if name == "CaseDurationService":
        from .service import CaseDurationService

        return CaseDurationService
    raise AttributeError(name)
