"""Generic interface to MongoDB objects and their methods (e.g. Collection) via PyMongo."""

from typing import Annotated, Any

import orjson
from pydantic import Field
from pymongo import MongoClient
from pymongo.cursor import Cursor
from bson.json_util import dumps
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
    default_title="Execute operation",
    description="Instantiate a PyMongo client and execute an operation on a Collection object.",
    display_group="PyMongo",
    doc_url="https://pymongo.readthedocs.io/en/stable/api/pymongo/asynchronous/collection.html",
    namespace="tools.pymongo",
    secrets=[mongodb_secret],
)
def execute_operation(
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
    # Connect to MongoDB (PyMongo's client is synchronous; context manager guarantees cleanup)
    connection_string = secrets.get("MONGODB_CONNECTION_STRING")
    with MongoClient(connection_string) as client:
        # Get the database and collection
        db = client[database_name]
        collection = db[collection_name]

        # Call the operation
        params = params or {}
        result = getattr(collection, operation_name)(**params)

        if isinstance(result, Cursor):
            # Force cursor evaluation so we can serialize the documents
            result = list(result)

        # pymongo uses bson which is not directly JSON serializable
        # https://pymongo.readthedocs.io/en/stable/api/bson/json_util.html
        # https://stackoverflow.com/questions/13241878/convert-pymongo-cursor-to-json
        return orjson.loads(dumps(result))
