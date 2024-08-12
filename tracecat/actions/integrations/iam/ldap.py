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
    def __init__(
        self,
        host: str,
        port: int,
        use_ssl: bool = False,
        is_active_directory: bool = False,
    ):
        self._server = ldap3.Server(
            host, port=int(port), use_ssl=use_ssl, get_info=ldap3.ALL
        )
        self._ldap_active_directory = is_active_directory

    def __enter__(self, *args, **kwargs):
        self.bind()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if self._connection:
            self._connection.unbind()

    def bind(self) -> bool:
        if self._connection is not None:
            return True

        self._connection = ldap3.Connection(
            self._server,
            os.getenv("LDAP_BIND_DN"),
            os.getenv("LDAP_BIND_PASS"),
            auto_bind=True,
        )

    def _search(self, base_dn: str, ldap_query: str):
        results = self._connection.search(
            base_dn, ldap_query, ldap3.SUBTREE, attributes=ldap3.ALL_ATTRIBUTES
        )
        if results:
            entries = []
            for entry in self._connection.entries:
                entries += [
                    {
                        "dn": entry.entry_dn,
                        "attributes": json.loads(entry.entry_to_json())[
                            "attributes"
                        ],  # entry is CaseInsensitiveDict containing other types that cannot be serialized
                    }
                ]
            return entries
        else:
            return []

    def _search_one(self, base_dn: str):
        results = self._connection.search(
            base_dn, "(objectClass=*)", ldap3.BASE, attributes=ldap3.ALL_ATTRIBUTES
        )
        if results:
            for entry in self._connection.entries:
                return dict(entry.attributes)

    def find_users(self, base_dn: str, search_value: str):
        filter = ""
        if self._ldap_active_directory:
            filter = f"(|(anr={search_value})(mail={search_value})(proxyAddresses=*:{search_value}))"
        else:
            filter = f"(anr={search_value})"
        return self._search(base_dn, filter)

    def get_user(self, user_dn: str) -> dict:
        return self._searchone(user_dn, "(objectClass=*)")

    def disable_user(self, user_dn: str):
        return self._connection.modify(user_dn, {"userAccountControl": [(2, [514])]})

    def enable_user(self, user_dn: str):
        return self._connection.modify(user_dn, {"userAccountControl": [(2, [512])]})


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
        use_ssl=(os.getenv("LDAP_SSL") == 1),
        is_active_directory=(os.getenv("LDAP_TYPE") == "AD"),
    )
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
    with create_ldap_client() as client:
        return client.find_users(base_dn, username_or_email)


@registry.register(
    default_title="Disable AD User",
    description="Disable AD user by distinguished name",
    display_group="LDAP",
    namespace="integrations.ldap",
    secrets=[ldap_secret],
)
async def disable_ad_user(
    user_dn: Annotated[
        str,
        Field(..., description="User distinguished name"),
    ],
) -> dict[str, Any]:
    with create_ldap_client() as client:
        result = client.disable_user(user_dn)
        if result:
            return {"success": True}
        else:
            return {"success": False, "error": client._connection.last_error}


@registry.register(
    default_title="Enable AD User",
    description="Enable AD user by distinguished name",
    display_group="LDAP",
    namespace="integrations.ldap",
    secrets=[ldap_secret],
)
async def enable_ad_user(
    user_dn: Annotated[
        str,
        Field(..., description="User distinguished name"),
    ],
) -> dict[str, Any]:
    with create_ldap_client() as client:
        result = client.enable_user(user_dn)
        if result:
            return {"success": True}
        else:
            return {"success": False, "error": client._connection.last_error}
