"""Action that uses Elasticsearch to normalize data into ECS format.

We use the Elasticsearch's REST API and ingest endpoints.
In particular, we use the `simulate` pipeline API to normalize data into ECS format without storing it.

Authentication (in order of precedence):
1. Connect to Elasticsearch in the local environment (port 9200).
2. Connect to Elasticsearch in external environement if `ELASTIC_API_KEY` and `ELASTIC_API_URL` secrets are set in Tracecat.

References
----------
- https://www.elastic.co/guide/en/elasticsearch/reference/current/simulate-pipeline-api.html
"""

import httpx

from yaml import safe_load
from tracecat.registry import Field, RegistrySecret, registry


elastic_secret = RegistrySecret(
    name="elastic",
    keys=["ELASTIC_API_KEY", "ELASTIC_API_URL"],
)
"""Elastic secret.

Secret
------
- name: `elastic`
- keys:
    - `ELASTIC_API_KEY`
    - `ELASTIC_API_URL`

Example Usage
-------------
Environment variable:
>>> os.environ["ELASTIC_API_KEY"]

Expression:
>>> ${{ SECRETS.elastic.ELASTIC_API_KEY }}
"""

import os
import orjson
from typing import Annotated, Any


@registry.register(
    default_title="Normalize events (ECS)",
    description="Normalize JSON objects into ECS format using an ingest pipeline.",
    display_group="Normalization",
    namespace="etl.normalization.ecs",
    secrets=[elastic_secret],
)
async def normalize_events(
    pipeline: Annotated[
        str | dict[str, Any],
        Field(..., description="Ingest pipeline definition. Can be a dictionary or URL to a YAML definition file."),
    ],
    data: Annotated[
        list[dict[str, Any]],
        Field(..., description="List of JSON objects to normalize into ECS format."),
    ],
) -> list[dict[str, Any]]:
    api_key = os.getenv("ELASTIC_API_KEY")
    api_url = os.getenv("ELASTIC_API_URL", "http://localhost:9200")

    url = f"{api_url}/_ingest/pipeline/_simulate"
    headers={"Content-Type": "application/json"}
    if api_key is not None:
        headers["Authorization"] = f"ApiKey {api_key}"

    if isinstance(pipeline, str):
        async with httpx.AsyncClient() as client:
            response = await client.get(pipeline)
            response.raise_for_status()
            pipeline = safe_load(response.text)

    docs = [{"_source": doc} for doc in data]
    query = {"pipeline": pipeline, "docs": docs}

    async with httpx.AsyncClient() as client:
        response = await client.post(url, headers=headers, json=query)
        response.raise_for_status()  # Raise an exception for HTTP errors
        normalized_docs = orjson.loads(response.text).get("docs", [])

    # Return the normalized data only
    return [doc.get("doc", {}).get("_source") for doc in normalized_docs]
