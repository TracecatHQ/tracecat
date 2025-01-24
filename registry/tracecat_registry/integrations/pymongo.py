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

"""MongoDB connection string.

- name: `mongodb`
- key:
    - `MONGODB_CONNECTION_STRING`
"""


@registry.register(
    default_title="Execute database operation",
    description="Instantiate a PyMongo client and execute an operation on a Collection object.",
    display_group="MongoDB",
    doc_url="https://pymongo.readthedocs.io/en/stable/api/pymongo/asynchronous/collection.html",
    namespace="integrations.mongodb",
    secrets=[mongodb_secret],
)
async def execute_operation(
    operation_name: Annotated[
        str,
        Field(
            ...,
            description="Operation to perform on the Collection, e.g. 'find', 'insert_one'.",
        ),
    ],
    database_name: Annotated[
        str,
        Field(..., description="Database to operate on"),
    ],
    collection_name: Annotated[
        str,
        Field(..., description="Collection to operate on"),
    ],
    params: Annotated[
        dict[str, Any] | None,
        Field(..., description="Parameters for the operation"),
    ] = None,
) -> dict[str, Any] | list[dict[str, Any]]:
    # Connect to MongoDB
    connection_string = secrets.get("MONGODB_CONNECTION_STRING")
    client = MongoClient(connection_string)

    # Get the database and collection
    db = client[database_name]
    collection = db[collection_name]

    # Call the operation
    params = params or {}
    result = getattr(collection, operation_name)(**params)

    if isinstance(result, Cursor):
        # Stringify the ObjectIDs
        result = [
            {**item, "_id": str(item["_id"])} if "_id" in item else item
            for item in list(result)
        ]
    elif isinstance(result, dict) and "_id" in result:
        result["_id"] = str(result["_id"])

    return result
