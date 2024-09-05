"""MongoDB Integration.

This integration will be used to query MongoDB for data.

Authentication: MongoDB Connection String

"""

import os
from typing import Annotated, Any

from pymongo import MongoClient

from tracecat.registry import Field, RegistrySecret, registry

mongo_secret = RegistrySecret(
    name="mongodb",
    keys=["MONGODB_CONNECTION_STRING"],
)

"""MongoDB Secret.

- name: `mongodb`
- key:
    - `MONGODB_CONNECTION_STRING`

"""


@registry.register(
    default_title="Query MongoDB",
    description="Queries MongoDB for data using specified search.",
    display_group="MongoDB",
    namespace="integrations.mongodb",
    secrets=[mongo_secret],
)
async def get_mongodb_document(
    search_field: Annotated[
        str,
        Field(..., description="The field to search for (i.e. 'elastic_namespace')."),
    ],
    search_value: Annotated[
        str,
        Field(
            ...,
            description="The value to search for in the specified field (i.e. 'customer_name').",
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
    return_field: Annotated[
        str | None,
        Field(
            None,
            description="Comma-separated list of fields you wish to return in this search. When left blank, the whole document is returned.",
        ),
    ] = None,
) -> dict[str, Any] | None:
    MONGODB_URI = os.getenv("MONGODB_CONNECTION_STRING")
    if not MONGODB_URI:
        raise ValueError("Missing MONGODB_CONNECTION_STRING")

    client = MongoClient(MONGODB_URI)
    db = client[database_name]
    collection = db[collection_name]

    # Build projection if return_field is provided
    if return_field:
        # Split the comma-separated fields and build the projection dict
        fields = return_field.split(",")
        projection = {field.strip(): 1 for field in fields}
    else:
        projection = None  # No projection, return entire document

    result = collection.find_one({search_field: search_value}, projection)

    if result:
        result["_id"] = str(result["_id"])  # Convert ObjectId to String
        return result
    else:
        return {"error_msg": "No results returned from MongoDB."}
