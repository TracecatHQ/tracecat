"""LDAP integration.

Authentication method: Token

Requires: A secret named `ldap` with the following keys:
- `LDAP_BIND_DN`
- `LDAP_BIND_PASS`

"""

import json
import os
from typing import Annotated, Any

import ldap3

from tracecat.registry import Field, RegistrySecret, registry

ldap_secret = RegistrySecret(
    name="ldap",
    keys=["LDAP_BIND_DN", "LDAP_BIND_PASS"],
)
"""LDAP secret.

- name: `ldap`
- keys:
    - `LDAP_BIND_DN`
    - `LDAP_BIND_PASS`
"""


class LdapClient:
    _ldap_server = None
    _ldap_connection = None
    _ldap_active_directory = False

    def __init__(
        self, host: str, port: int, ssl: bool = False, active_directory: bool = False
    ):
        self._ldap_server = ldap3.Server(host, int(port), ssl, get_info=ldap3.ALL)
        self._ldap_active_directory = active_directory

    def __enter__(self, *args, **kwargs):
        self.bind()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if self._ldap_connection:
            self._ldap_connection.unbind()

    async def __aenter__(self, *args, **kwargs):
        self.bind()
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        if self._ldap_connection:
            self._ldap_connection.unbind()

    def bind(self) -> bool:
        if self._ldap_connection is not None:
            return True

        self._ldap_connection = ldap3.Connection(
            self._ldap_server,
            os.getenv("LDAP_BIND_DN"),
            os.getenv("LDAP_BIND_PASS"),
            auto_bind=True,
        )

    def _search(self, base_dn: str, ldap_query: str):
        results = self._ldap_connection.search(
            base_dn, ldap_query, ldap3.SUBTREE, attributes=ldap3.ALL_ATTRIBUTES
        )
        if results:
            entries = {}
            for entry in self._ldap_connection.entries:
                entries[entry.entry_dn] = {
                    "attributes": dict(entry.entry_raw_attributes)
                }
            return entries
        else:
            return []

    def find_users(self, base_dn: str, search_value: str):
        filter = ""
        if self._ldap_active_directory:
            filter = f"(|(anr={search_value})(mail={search_value})(proxyAddresses=*:{search_value}))"
        else:
            filter = f"(anr={search_value})"
        return self._search(base_dn, filter)


def create_ldap_client() -> LdapClient:
    LDAP_BIND_DN = os.getenv("LDAP_BIND_DN")
    LDAP_BIND_PASS = os.getenv("LDAP_BIND_PASS")

    if LDAP_BIND_DN is None:
        raise ValueError("LDAP_BIND_DN is not set")
    if LDAP_BIND_PASS is None:
        raise ValueError("LDAP_BIND_PASS is not set")
    client = LdapClient(
        host=os.getenv("LDAP_HOST"),
        port=os.getenv("LDAP_PORT"),
        ssl=(os.getenv("LDAP_SSL") == 1),
        active_directory=(os.getenv("LDAP_TYPE") == "AD"),
    )
    client.bind()
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
        return json.dumps(users)
