from pycti import OpenCTIApiClient
import inspect

from typing import Annotated, Any

import pycti.entities
from tracecat_registry import RegistrySecret, registry, secrets
from typing_extensions import Doc


opencti_secret = RegistrySecret(name="opencti", keys=["OPENCTI_API_TOKEN"])
"""OpenCTI API token.

- name: `opencti`
- keys:
    - `OPENCTI_API_TOKEN`
"""


def _mock_client():
    return OpenCTIApiClient(
        url="https://localhost:4434",
        token="test-token",
        perform_health_check=False,
    )


@registry.register(
    default_title="List entity types",
    description="List all entity types in OpenCTI.",
    display_group="OpenCTI",
    doc_url="https://pycti.readthedocs.io/en/latest/api_client.html",
    namespace="tools.opencti",
)
def list_entity_types() -> list[str]:
    """Extract all entities from the OpenCTI API client and return a list of their names."""

    def _is_entity(obj):
        return hasattr(obj, "__class__") and obj.__class__.__name__ in opencti_entities

    client = _mock_client()
    # Create a set of names from pycti.__all__ for efficient lookup
    opencti_entities = set(pycti.__all__)
    # Lambda that checks if the object's name is in opencti_entities
    entities = [name for name, _ in inspect.getmembers(client, _is_entity)]
    return entities


@registry.register(
    default_title="List entity API methods",
    description="List all API methods for a given entity type.",
    display_group="OpenCTI",
    doc_url="https://pycti.readthedocs.io/en/latest/api_client.html",
    namespace="tools.opencti",
)
def list_entity_methods(
    entity_name: Annotated[str, Doc("Entity type to list methods for.")],
) -> list[str]:
    """List all API methods for a given entity type."""
    client = _mock_client()
    entity = getattr(client, entity_name)
    return [
        name
        for name, _ in inspect.getmembers(entity, inspect.ismethod)
        if not name.startswith("_")
    ]


@registry.register(
    default_title="Call entity API method",
    description="Call an entity API method.",
    display_group="OpenCTI",
    doc_url="https://github.com/OpenCTI-Platform/client-python",
    namespace="tools.opencti",
    secrets=[opencti_secret],
)
def call_api(
    entity_name: Annotated[str, Doc("Entity type to call method for.")],
    method_name: Annotated[str, Doc("Method to call.")],
    params: Annotated[dict[str, Any], Doc("Parameters to pass to the method.")],
    base_url: Annotated[str, Doc("Base URL to OpenCTI API.")],
    ssl_verify: Annotated[bool, Doc("Whether to verify SSL certificates.")] = True,
) -> list[dict[str, Any]]:
    """Call a method on an entity type."""
    client = OpenCTIApiClient(
        url=base_url,
        token=secrets.get("OPENCTI_API_TOKEN"),
        ssl_verify=ssl_verify,
    )
    entity = getattr(client, entity_name)
    result = getattr(entity, method_name)(**params)
    return result
