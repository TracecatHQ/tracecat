"""LDAP integration.

Authentication method: Token

Requires: A secret named `ldap` with the following keys:
- `LDAP_BIND_DN`
- `LDAP_BIND_PASS`

"""

from typing import Annotated, Any, Protocol

import ldap3
import orjson
from pydantic import Field

from tracecat_registry import RegistrySecret, registry, secrets

ldap_secret = RegistrySecret(
    name="ldap",
    keys=["LDAP_HOST", "LDAP_PORT", "LDAP_USER", "LDAP_PASSWORD"],
)
"""LDAP secret.

- name: `ldap`
- keys:
    - `LDAP_HOST`
    - `LDAP_PORT`
    - `LDAP_USER`
    - `LDAP_PASSWORD`
"""


class LdapConnectionProtocol(Protocol):
    @property
    def entries(self) -> list[Any]: ...

    @property
    def result(self) -> dict[str, Any]: ...

    def unbind(self) -> None: ...

    def add(
        self,
        dn: str,
        object_class: str,
        attributes: dict[str, Any],
    ) -> bool: ...

    def delete(self, dn: str) -> bool: ...

    def modify(
        self,
        dn: str,
        changes: dict[str, list[tuple[str, list[str | int]]]],
    ) -> bool: ...

    def search(self, *args: Any, **kwargs: Any) -> bool: ...


class LdapClient:
    def __init__(
        self,
        host: str,
        port: int,
        user: str,
        password: str,
        server_kwargs: dict[str, Any] | None = None,
        connection_kwargs: dict[str, Any] | None = None,
    ):
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.server_kwargs = server_kwargs or {}
        self.connection_kwargs = connection_kwargs or {}
        self.connection: LdapConnectionProtocol | None = None

    def __enter__(self):
        server = ldap3.Server(
            self.host,
            port=self.port,
            **self.server_kwargs,
        )
        self.connection = ldap3.Connection(
            server,
            user=self.user,
            password=self.password,
            auto_bind=True,
            auto_escape=True,
            **self.connection_kwargs,
        )
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if self.connection:
            self.connection.unbind()

    def _get_connection(self) -> LdapConnectionProtocol:
        if not self.connection:
            msg = "LDAP connection has not been established"
            raise RuntimeError(msg)
        return self.connection

    def add_entry(
        self,
        dn: str,
        object_class: str,
        attributes: dict[str, Any],
    ) -> None:
        connection = self._get_connection()
        result = connection.add(
            dn,
            object_class=object_class,
            attributes=attributes,
        )
        if not result:
            raise PermissionError(connection.result)

    def delete_entry(self, dn: str) -> None:
        connection = self._get_connection()
        result = connection.delete(dn)
        if not result:
            raise PermissionError(connection.result)

    def modify_entry(
        self,
        dn: str,
        changes: dict[str, list[tuple[str, list[str | int]]]],
    ) -> None:
        connection = self._get_connection()
        result = connection.modify(dn, changes)
        if not result:
            raise PermissionError(connection.result)

    def search(
        self,
        search_base: str,
        search_filter: str,
    ) -> list[dict[str, Any]]:
        """Search the LDAP directory.

        Args:
            search_base: The base of the search request.
            search_filter: The filter of the search request (RFC4515 syntax).

        Returns:
            List of search result entries, each containing 'dn' and 'attributes'.

        Raises:
            RuntimeError: If the search operation fails.
        """
        connection = self._get_connection()

        result = connection.search(
            search_base=search_base,
            search_filter=search_filter,
            search_scope=ldap3.SUBTREE,
            attributes=ldap3.ALL_ATTRIBUTES,
        )

        if result:
            # Format entries as list of dicts with dn and attributes
            entries = []
            for entry in connection.entries:
                # Use entry_to_json() to handle serialization of ldap3 types
                entry_json = orjson.loads(entry.entry_to_json())
                entries.append(
                    {
                        "dn": entry.entry_dn,
                        "attributes": entry_json["attributes"],
                    }
                )

            return entries
        else:
            # Search operation failed - check connection.result for error details
            error_msg = f"LDAP search failed: {connection.result}"
            raise RuntimeError(error_msg)


def get_ldap_secrets() -> dict[str, Any]:
    return {
        "host": secrets.get("LDAP_HOST"),
        "port": int(secrets.get("LDAP_PORT")),
        "user": secrets.get("LDAP_USER"),
        "password": secrets.get("LDAP_PASSWORD"),
    }


@registry.register(
    default_title="Add LDAP entry",
    description="Add an entry to the LDAP directory.",
    display_group="LDAP",
    doc_url="https://ldap3.readthedocs.io/en/latest/add.html",
    namespace="tools.ldap",
    secrets=[ldap_secret],
)
def add_entry(
    dn: Annotated[str, Field(..., description="Distinguished name of the entry")],
    object_class: Annotated[str, Field(..., description="Object class of the entry")],
    attributes: Annotated[
        dict[str, Any], Field(..., description="Attributes of the entry")
    ],
    server_kwargs: Annotated[
        dict[str, Any] | None, Field(..., description="Additional server parameters")
    ] = None,
    connection_kwargs: Annotated[
        dict[str, Any] | None,
        Field(..., description="Additional connection parameters"),
    ] = None,
) -> None:
    with LdapClient(
        **get_ldap_secrets(),
        server_kwargs=server_kwargs,
        connection_kwargs=connection_kwargs,
    ) as client:
        return client.add_entry(dn, object_class, attributes)


@registry.register(
    default_title="Delete LDAP entry",
    description="Delete an entry from the LDAP directory.",
    display_group="LDAP",
    doc_url="https://ldap3.readthedocs.io/en/latest/delete.html",
    namespace="tools.ldap",
    secrets=[ldap_secret],
)
def delete_entry(
    dn: Annotated[str, Field(..., description="Distinguished name of the entry")],
    server_kwargs: Annotated[
        dict[str, Any] | None, Field(..., description="Additional server parameters")
    ] = None,
    connection_kwargs: Annotated[
        dict[str, Any] | None,
        Field(..., description="Additional connection parameters"),
    ] = None,
) -> None:
    with LdapClient(
        **get_ldap_secrets(),
        server_kwargs=server_kwargs,
        connection_kwargs=connection_kwargs,
    ) as client:
        return client.delete_entry(dn)


@registry.register(
    default_title="Modify LDAP entry",
    description="Modify an LDAP entry in the directory.",
    display_group="LDAP",
    doc_url="https://ldap3.readthedocs.io/en/latest/modify.html",
    namespace="tools.ldap",
    secrets=[ldap_secret],
)
def modify_entry(
    dn: Annotated[str, Field(..., description="Distinguished name of the entry")],
    changes: Annotated[
        dict[str, list[tuple[str, list[str | int]]]],
        Field(..., description="Changes to the entry"),
    ],
    server_kwargs: Annotated[
        dict[str, Any] | None, Field(..., description="Additional server parameters")
    ] = None,
    connection_kwargs: Annotated[
        dict[str, Any] | None,
        Field(..., description="Additional connection parameters"),
    ] = None,
) -> None:
    with LdapClient(
        **get_ldap_secrets(),
        server_kwargs=server_kwargs,
        connection_kwargs=connection_kwargs,
    ) as client:
        return client.modify_entry(dn, changes)


@registry.register(
    default_title="Search LDAP directory",
    description="Search the LDAP directory for entries matching the query.",
    display_group="LDAP",
    doc_url="https://ldap3.readthedocs.io/en/latest/searches.html",
    namespace="tools.ldap",
    secrets=[ldap_secret],
)
def search_entries(
    search_base: Annotated[str, Field(..., description="Search base DN")],
    search_filter: Annotated[
        str,
        Field(
            ...,
            description=(
                "LDAP search filter (RFC4515 syntax). "
                "Example: '(cn=John*)' or '(&(objectClass=person)(mail=*@example.com))'"
            ),
        ),
    ],
    server_kwargs: Annotated[
        dict[str, Any] | None, Field(..., description="Additional server parameters")
    ] = None,
    connection_kwargs: Annotated[
        dict[str, Any] | None,
        Field(..., description="Additional connection parameters"),
    ] = None,
) -> list[dict[str, Any]]:
    """Search the LDAP directory for entries matching the query.

    Returns a list of entries, each containing:
    - dn: Distinguished name of the entry
    - attributes: Dictionary mapping attribute names to lists of values
    """
    with LdapClient(
        **get_ldap_secrets(),
        server_kwargs=server_kwargs,
        connection_kwargs=connection_kwargs,
    ) as client:
        return client.search(
            search_base=search_base,
            search_filter=search_filter,
        )
