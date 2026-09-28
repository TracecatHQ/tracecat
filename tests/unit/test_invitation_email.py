"""Tests for invitation email rendering."""

from __future__ import annotations

import pytest

from tracecat import config
from tracecat.invitations.email import (
    build_accept_url,
    invitation_email,
    render_invitation_email,
)


def test_invitation_template_escapes_html_and_subject_controls() -> None:
    subject, html, text = render_invitation_email(
        accept_url='https://app.example.com/invitations/accept?token=a&b="x',
        organization_name="Acme\r\nBcc: attacker@example.com<script>",
    )

    assert "\r" not in subject
    assert "\n" not in subject
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "token=a&amp;b=&quot;x" in html
    assert 'token=a&b="x' in text


def test_build_accept_url_handles_trailing_slash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__PUBLIC_APP_URL", "https://app.example.com/")

    assert build_accept_url("token-123") == (
        "https://app.example.com/invitations/accept?token=token-123"
    )


def test_invitation_logo_url_handles_trailing_slash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__PUBLIC_APP_URL", "https://app.example.com/")

    _, html, _ = render_invitation_email(
        accept_url="https://app.example.com/invitations/accept?token=t",
        organization_name="Acme",
    )

    assert 'src="https://app.example.com/icon.png"' in html


def test_invitation_email_renders_subject_and_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__PUBLIC_APP_URL", "https://app.example.com")

    message = invitation_email(
        to="invitee@example.com", organization_name="Acme", token="token-123"
    )

    assert message.to == ("invitee@example.com",)
    assert message.subject == "Join Acme on Tracecat"
    assert "token-123" in message.text
