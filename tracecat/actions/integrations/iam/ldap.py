"""LDAP integration.

Authentication method: Token

Requires: A secret named `ldap` with the following keys:
- `LDAP_BIND_USER`
- `LDAP_BIND_PASS`

"""

import os
from typing import Annotated, Any

import ldap3

from tracecat.registry import Field, RegistrySecret, registry

ldap_secret = RegistrySecret(
    name="ldap",
    keys=["LDAP_BIND_USER", "LDAP_BIND_PASS"],
)
"""LDAP secret.

- name: `ldap`
- keys:
    - `LDAP_BIND_USER`
    - `LDAP_BIND_PASS`
"""


class LdapClient:
    _ldap_server = None
    _ldap_connection = None
    _ldap_active_directory = False

    def __init__(
        self, host: str, port: int, ssl: bool = False, active_directory: bool = False
    ):
        self._ldap_server = ldap3.Server(host, port, ssl, get_info=ldap3.ALL)
        self._ldap_active_directory = active_directory

    def bind(self, user: str, password: str) -> bool:
        if self._ldap_connection is not None:
            return True

        self._ldap_connection = ldap3.Connection(
            self._ldap_server, user, password, auto_bind=True
        )

    def _search(self, base_dn: str, ldap_query: str):
        results = self._ldap_connection.search(
            base_dn, ldap_query, ldap3.SUBTREE, attributes=ldap3.ALL_ATTRIBUTES
        )
        if results[0]:
            return self._ldap_connection.entries

    def find_users(self, base_dn: str, search_value: str):
        filter = ""
        if self._ldap_active_directory:
            filter = "(|(anr={user})(mail={user})(proxyAddresses=*:{user}))"
        else:
            filter = "(anr={user})"
        return self._search(base_dn, filter)


def create_ldap_client() -> LdapClient:
    LDAP_BIND_USER = os.getenv("LDAP_BIND_USER")
    LDAP_BIND_PASS = os.getenv("LDAP_BIND_PASS")

    if LDAP_BIND_USER is None:
        raise ValueError("LDAP_BIND_USER is not set")
    if LDAP_BIND_PASS is None:
        raise ValueError("LDAP_BIND_PASS is not set")
    client = LdapClient(
        os.getenv("LDAP_HOST"),
        os.getenv("LDAP_PORT"),
        os.getenv("LDAP_SSL") == 1,
    )
    client.bind(LDAP_BIND_USER, LDAP_BIND_PASS)
    return client


@registry.register(
    default_title="Find LDAP Users",
    description="Find LDAP users by login username or email",
    display_group="LDAP",
    namespace="integrations.ldap",
    secrets=[ldap_secret],
)
async def find_ldap_users(
    username_or_email: Annotated[
        str,
        Field(..., description="Login username or e-mail to find"),
    ],
    base_dn: Annotated[
        str,
        Field(..., description="Search base DN for querying LDAP"),
    ],
) -> list[dict[str, Any]]:
    async with create_ldap_client() as client:
        users = client.find_users(base_dn, username_or_email)
        return users.json()
