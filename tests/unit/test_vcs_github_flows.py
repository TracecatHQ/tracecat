import pytest

from tracecat.vcs.github.flows import installation_redirect_url


@pytest.mark.parametrize(
    ("slug", "owner_login", "owner_type", "expected"),
    [
        (
            "tracecat-sync",
            "acme-sec",
            "Organization",
            "https://github.com/organizations/acme-sec/settings/installations",
        ),
        (
            "tracecat-sync",
            "octo-user",
            "User",
            "https://github.com/apps/tracecat-sync/installations/new",
        ),
        (
            "tracecat-sync",
            None,
            None,
            "https://github.com/apps/tracecat-sync/installations/new",
        ),
        (None, "acme-sec", "Organization", "https://github.com/settings/apps"),
    ],
)
def test_installation_redirect_url(
    slug: str | None,
    owner_login: str | None,
    owner_type: str | None,
    expected: str,
) -> None:
    assert (
        installation_redirect_url(
            slug=slug, owner_login=owner_login, owner_type=owner_type
        )
        == expected
    )
