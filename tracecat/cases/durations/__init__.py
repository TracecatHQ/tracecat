"""Case duration metric models and services."""

from .schemas import (
    CaseDurationAnchorSelection,
    CaseDurationComputation,
    CaseDurationCreate,
    CaseDurationDefinitionCreate,
    CaseDurationDefinitionRead,
    CaseDurationDefinitionUpdate,
    CaseDurationEventAnchor,
    CaseDurationRead,
    CaseDurationUpdate,
)

__all__ = [
    "CaseDurationAnchorSelection",
    "CaseDurationComputation",
    "CaseDurationCreate",
    "CaseDurationDefinitionCreate",
    "CaseDurationDefinitionRead",
    "CaseDurationDefinitionService",  # pyright: ignore[reportUnsupportedDunderAll]
    "CaseDurationDefinitionUpdate",
    "CaseDurationEventAnchor",
    "CaseDurationRead",
    "CaseDurationService",  # pyright: ignore[reportUnsupportedDunderAll]
    "CaseDurationUpdate",
]


def __getattr__(name: str):
    if name == "CaseDurationService":
        from .service import CaseDurationService

        return CaseDurationService
    if name == "CaseDurationDefinitionService":
        from .service import CaseDurationDefinitionService

        return CaseDurationDefinitionService
    raise AttributeError(name)
