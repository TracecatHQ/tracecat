"""Generic interface to MongoDB objects and their methods (e.g. Collection) via PyMongo."""

from typing import Annotated, Any

from pydantic import Field
from pymongo import MongoClient
from pymongo.cursor import Cursor

from tracecat_registry import RegistrySecret, registry, secrets

mongodb_secret = RegistrySecret(
    name="mongodb",
    keys=["MONGODB_CONNECTION_STRING"],
)

"""MongoDB Secret.

- name: `mongodb`
- key:
    - `MONGODB_CONNECTION_STRING`
"""


@registry.register(
    default_title="Perform MongoDB CRUD",
    description="Performs a MongoDB operation on a specified collection.",
    display_group="MongoDB",
    namespace="integrations.mongodb",
    secrets=[mongodb_secret],
)
async def perform_mongodb_crud(
    operation: Annotated[
        str,
        Field(
            ...,
            description="Operation to perform on the MongoDB Collection, e.g. 'find', 'insert_one'.",
        ),
    ],
    database_name: Annotated[
        str,
        Field(..., description="The name of the target database."),
    ],
    collection_name: Annotated[
        str,
        Field(..., description="The name of the target collection"),
    ],
    params: Annotated[
        dict[str, Any],
        Field(..., description="Parameters for the operation."),
    ] = None,
) -> dict[str, Any] | list[dict[str, Any]]:
    params = params or {}
    connection_string = secrets.get("MONGODB_CONNECTION_STRING")
    client = MongoClient(connection_string)
    db = client[database_name]
    collection = db[collection_name]
    result = getattr(collection, operation)(**params)

    if isinstance(result, Cursor):
        # Stringify the ObjectIDs
        result = [
            {**item, "_id": str(item["_id"])} if "_id" in item else item
            for item in list(result)
        ]
    elif isinstance(result, dict) and "_id" in result:
        result["_id"] = str(result["_id"])

    return result
