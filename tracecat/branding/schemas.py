"""API schemas for organization branding."""

from pydantic import Field

from tracecat.core.schemas import Schema


class BrandingRead(Schema):
    """Effective branding for an organization, safe to show before sign-in.

    Unset fields mean the Tracecat default. Organizations without the
    ``custom_branding`` entitlement always get the defaults.
    """

    logo: str | None = Field(default=None, description="Logo as a data URL")
    primary_color_light: str | None = Field(
        default=None, description="Light mode primary color as #RRGGBB"
    )
    primary_color_dark: str | None = Field(
        default=None, description="Dark mode primary color as #RRGGBB"
    )
    terms_enabled: bool = Field(
        default=True, description="Whether to show the terms notice on sign-in"
    )
