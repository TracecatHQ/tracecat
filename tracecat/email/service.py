"""Invitation-email orchestration helpers."""

from __future__ import annotations

from tracecat import config


def build_accept_url(token: str) -> str:
    """Build the invitation-accept URL for an invitation token."""
    return f"{config.TRACECAT__PUBLIC_APP_URL}/invitations/accept?token={token}"
