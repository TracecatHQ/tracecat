"""Tier management for organizations.

Imports are deferred to avoid circular imports with db.models.
Use explicit imports from submodules when needed at module load time.
"""

from tracecat.tiers.types import EntitlementsDict

# Lazy exports - import from submodules directly to avoid circular imports
__all__ = [
    "EntitlementsDict",
]


def __getattr__(name: str):
    """Lazy import to avoid circular imports."""
    if name == "DEFAULT_ENTITLEMENTS":
        from tracecat.tiers.defaults import DEFAULT_ENTITLEMENTS

        return DEFAULT_ENTITLEMENTS
    if name == "DEFAULT_LIMITS":
        from tracecat.tiers.defaults import DEFAULT_LIMITS

        return DEFAULT_LIMITS
    if name == "DEFAULT_TIER_DISPLAY_NAME":
        from tracecat.tiers.defaults import DEFAULT_TIER_DISPLAY_NAME

        return DEFAULT_TIER_DISPLAY_NAME
    if name == "DEFAULT_TIER_ID":
        from tracecat.tiers.defaults import DEFAULT_TIER_ID

        return DEFAULT_TIER_ID
    if name == "Entitlement":
        from tracecat.tiers.entitlements import Entitlement

        return Entitlement
    if name == "EntitlementService":
        from tracecat.tiers.entitlements import EntitlementService

        return EntitlementService
    if name == "check_entitlement":
        from tracecat.tiers.entitlements import check_entitlement

        return check_entitlement
    if name == "TierService":
        from tracecat.tiers.service import TierService

        return TierService
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
