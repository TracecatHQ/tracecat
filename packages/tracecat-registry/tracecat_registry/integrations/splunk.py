"""Utilities for loading CSV files into Splunk KV Store collections."""

from __future__ import annotations

import csv
import io
from typing import Annotated, Any, Iterable, Literal

import httpx
from typing_extensions import Doc

from tracecat_registry import RegistrySecret, registry, secrets


splunk_secret = RegistrySecret(
    name="splunk",
    keys=["SPLUNK_API_KEY"],
)
"""Splunk API token.

- name: `splunk`
- keys:
    - `SPLUNK_API_KEY`
"""


class SplunkKVStoreError(RuntimeError):
    """Raised when a Splunk KV Store operation fails."""


def _iter_csv_rows(csv_text: str) -> Iterable[dict[str, str]]:
    reader = csv.DictReader(io.StringIO(csv_text))
    if not reader.fieldnames:
        msg = "CSV is missing a header row."
        raise ValueError(msg)

    for row in reader:
        if row is None:
            continue

        # Skip empty rows to avoid creating blank documents
        if not any(value for value in row.values() if value is not None):
            continue

        yield {key: value or "" for key, value in row.items()}


def _raise_for_status(response: httpx.Response, action: str) -> None:
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        detail = response.text
        msg = f"{action} failed ({response.status_code}): {detail}"
        raise SplunkKVStoreError(msg) from exc


async def _collection_exists(
    client: httpx.AsyncClient,
    *,
    owner: str,
    app: str,
    collection: str,
    headers: dict[str, str],
) -> bool:
    response = await client.get(
        f"/servicesNS/{owner}/{app}/storage/collections/config/{collection}",
        headers=headers,
        params={"output_mode": "json"},
    )
    if response.status_code == 404:
        return False
    _raise_for_status(response, f"Check collection '{collection}'")
    return True


async def _create_collection(
    client: httpx.AsyncClient,
    *,
    owner: str,
    app: str,
    collection: str,
    headers: dict[str, str],
) -> None:
    response = await client.post(
        f"/servicesNS/{owner}/{app}/storage/collections/config",
        headers={**headers, "Content-Type": "application/x-www-form-urlencoded"},
        data={"name": collection, "output_mode": "json"},
    )
    _raise_for_status(response, f"Create collection '{collection}'")


async def _delete_collection(
    client: httpx.AsyncClient,
    *,
    owner: str,
    app: str,
    collection: str,
    headers: dict[str, str],
) -> None:
    response = await client.delete(
        f"/servicesNS/{owner}/{app}/storage/collections/config/{collection}",
        headers=headers,
        params={"output_mode": "json"},
    )
    _raise_for_status(response, f"Delete collection '{collection}'")


async def _upload_batch(
    client: httpx.AsyncClient,
    *,
    owner: str,
    app: str,
    collection: str,
    headers: dict[str, str],
    entries: list[dict[str, Any]],
) -> None:
    response = await client.post(
        f"/servicesNS/{owner}/{app}/storage/collections/data/{collection}/batch_save",
        headers=headers,
        params={"output_mode": "json"},
        json=entries,
    )
    if response.status_code in (404, 405):
        # Fallback for Splunk instances without batch_save support
        for entry in entries:
            single_response = await client.post(
                f"/servicesNS/{owner}/{app}/storage/collections/data/{collection}",
                headers=headers,
                params={"output_mode": "json"},
                json=entry,
            )
            _raise_for_status(
                single_response, f"Create entry in collection '{collection}'"
            )
        return

    _raise_for_status(response, f"Upload batch to collection '{collection}'")


async def _download_csv(
    url: str, *, headers: dict[str, str] | None, verify_ssl: bool
) -> str:
    async with httpx.AsyncClient(
        follow_redirects=True,
        verify=verify_ssl,
        timeout=httpx.Timeout(10.0, read=60.0),
    ) as client:
        try:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            return response.text
        except httpx.RequestError as exc:
            msg = f"Failed to download CSV from {url!r}: {exc}"
            raise SplunkKVStoreError(msg) from exc
        except httpx.HTTPStatusError as exc:
            msg = f"Failed to download CSV from {url!r} ({exc.response.status_code}): {exc.response.text}"
            raise SplunkKVStoreError(msg) from exc


@registry.register(
    default_title="Upload CSV to KV Collection",
    description=(
        "Download a CSV file and upload its rows to a Splunk KV Store collection with "
        "create, append, or override modes."
    ),
    display_group="Splunk",
    doc_url="https://help.splunk.com/en/splunk-enterprise/rest-api-reference/9.4/kv-store-endpoints",
    namespace="tools.splunk",
    secrets=[splunk_secret],
)
async def upload_csv_to_kv_collection(
    csv_url: Annotated[str, Doc("URL pointing to the CSV file to ingest.")],
    collection: Annotated[str, Doc("Name of the KV Store collection to target.")],
    mode: Annotated[
        Literal["create", "append", "override"],
        Doc(
            "create: new collection, error if it exists. "
            "append: add to existing collection, error if missing. "
            "override: replace existing collection if present."
        ),
    ] = "create",
    owner: Annotated[
        str, Doc('Splunk namespace owner (use "nobody" for shared access).')
    ] = "nobody",
    app: Annotated[str, Doc("Splunk app context (e.g. search).")] = "search",
    csv_headers: Annotated[
        dict[str, str] | None,
        Doc("Optional HTTP headers for downloading the CSV (e.g. Authorization)."),
    ] = None,
    batch_size: Annotated[
        int,
        Doc(
            "Number of CSV rows to send per request. Lower this if you hit payload limits."
        ),
    ] = 500,
    base_url: Annotated[
        str | None,
        Doc(
            "Splunk base URL (e.g. https://localhost:8089 or https://example.splunkcloud.com:8089). "
            "This parameter is required."
        ),
    ] = None,
    verify_ssl: Annotated[
        bool, Doc("Whether to verify SSL certificates when downloading and uploading.")
    ] = True,
) -> dict[str, Any]:
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")

    if not base_url:
        raise SplunkKVStoreError(
            "Splunk base_url is required. Pass it directly as a parameter."
        )

    token = secrets.get("SPLUNK_API_KEY")
    csv_text = await _download_csv(csv_url, headers=csv_headers, verify_ssl=verify_ssl)
    rows = _iter_csv_rows(csv_text)

    common_headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }

    async with httpx.AsyncClient(
        base_url=base_url,
        follow_redirects=True,
        verify=verify_ssl,
        timeout=httpx.Timeout(10.0, read=60.0),
    ) as client:
        exists = await _collection_exists(
            client,
            owner=owner,
            app=app,
            collection=collection,
            headers=common_headers,
        )

        collection_created = False
        if mode == "create":
            if exists:
                msg = f"Collection '{collection}' already exists."
                raise SplunkKVStoreError(msg)
            await _create_collection(
                client,
                owner=owner,
                app=app,
                collection=collection,
                headers=common_headers,
            )
            collection_created = True
        elif mode == "append":
            if not exists:
                msg = f"Collection '{collection}' does not exist; append mode cannot create it."
                raise SplunkKVStoreError(msg)
        elif mode == "override":
            if exists:
                await _delete_collection(
                    client,
                    owner=owner,
                    app=app,
                    collection=collection,
                    headers=common_headers,
                )
            await _create_collection(
                client,
                owner=owner,
                app=app,
                collection=collection,
                headers=common_headers,
            )
            collection_created = True
        else:
            msg = f"Unsupported mode {mode!r}"
            raise SplunkKVStoreError(msg)

        total_rows = 0
        batch: list[dict[str, Any]] = []
        for row in rows:
            batch.append(row)
            if len(batch) >= batch_size:
                await _upload_batch(
                    client,
                    owner=owner,
                    app=app,
                    collection=collection,
                    headers=common_headers,
                    entries=batch,
                )
                total_rows += len(batch)
                batch.clear()

        if batch:
            await _upload_batch(
                client,
                owner=owner,
                app=app,
                collection=collection,
                headers=common_headers,
                entries=batch,
            )
            total_rows += len(batch)

    return {
        "collection": collection,
        "mode": mode,
        "rows_processed": total_rows,
        "collection_created": collection_created,
    }
