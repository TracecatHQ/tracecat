"""Integrations with Datadog security monitoring API.

Inputs and outputs are denoised and normalized.
The interface is an opintionated take on the request / responses most relevant for high-fidelity alert management.
This is not a 1-to-1 mapping to the Datadog API.

Required credentials: `datadog-security-monitoring` secret with `DD_API_KEY` and `DD_APP_KEY` keys.

API reference: https://docs.datadoghq.com/api/latest/security-monitoring
"""

import os
from datetime import datetime
from typing import Any, Literal

import httpx
import orjson
import polars as pl
import polars.selectors as cs

from tracecat.integrations._registry import registry
from tracecat.logger import standard_logger

logger = standard_logger(__name__)


DD_REGION_TO_URL = {
    "ap1": "https://api.ap1.datadoghq.com",
    "eu1": "https://api.datadoghq.eu",
    "us1": "https://api.datadoghq.com",
    "us3": "https://api.us3.datadoghq.com",
    "us5": "https://api.us5.datadoghq.com",
}


def create_datadog_client(region: str):
    base_url = DD_REGION_TO_URL.get(region, DD_REGION_TO_URL["us1"])
    dd_api_key = os.environ["DD_API_KEY"]
    dd_app_key = os.environ["DD_APP_KEY"]
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "DD-API-KEY": dd_api_key,
        "DD-APPLICATION-KEY": dd_app_key,
    }
    return httpx.Client(base_url=base_url, headers=headers)


@registry.register(
    description="Get Datadog SIEM security signals. Requires `security_monitoring_signals_read` scope.",
    secrets=["datadog-security-monitoring"],
)
def list_security_signals(
    query: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int = 100,
    region: str = "us1",
) -> list[dict[str, Any]]:
    """Return list of security signals."""
    body = {
        "filter": {
            # Assume UTC
            "from": start.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
            "to": end.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
            "query": query,
        },
        "page": {"limit": limit},
    }
    with create_datadog_client(region=region) as client:
        rsp = client.post(
            "/api/v2/security_monitoring/signals", data=orjson.dumps(body)
        )
        rsp.raise_for_status()

    events = rsp.json().get("data", [])
    signals = (
        pl.from_dicts(events)
        .unnest("attributes")
        # TODO: Select relevant columns
        # .select([])
        .to_dicts()
    )
    return signals


@registry.register(
    description="Update Datadog SIEM security signal's triage state. Requires `security_monitoring_signals_write` scope.",
    secrets=["datadog-security-monitoring"],
)
def update_security_signal_state(
    state: Literal[
        "none",
        "false_positive",
        "testing_or_maintenance",
        "investigated_case_opened",
        "other",
    ],
    archive_comment: str | None = None,
    archive_reason: str | None = None,
    region: str = "us1",
) -> dict[str, Any]:
    """Return updated security signal object."""
    with create_datadog_client(region=region) as client:
        rsp = client.patch(
            "/api/v2/security_monitoring/signals/{signal_id}/state",
            data=orjson.dumps(
                {
                    "state": state,
                    "archive_comment": archive_comment,
                    "archive_reason": archive_reason,
                }
            ),
        )
        rsp.raise_for_status()
    return rsp.json()


@registry.register(
    description="List Datadog SIEM detection rules. Requires `security_monitoring_rules_read` scope.",
    secrets=["datadog-security-monitoring"],
)
def list_detection_rules(region: str = "us1") -> list[dict]:
    """Return list of detection rules."""
    page_size = 100
    max_pages = 20  # In reality ~10-15 pages only, but this is a safety net

    rules = []
    for i in range(max_pages):
        with create_datadog_client(region=region) as client:
            rsp = client.get(
                "/api/v2/security_monitoring/rules",
                params={"page[size]": page_size, "page[number]": i},
            )
            rsp.raise_for_status()
            obj = rsp.json()
        # Unpack rules
        listed_rules = obj["data"]
        rules.extend(listed_rules)
        if len(listed_rules) < page_size:
            break

    # NOTE: Not all rules are log detections
    detection_rules = (
        pl.LazyFrame(rules)
        .unique("id")
        .explode("tags")
        .with_columns(
            tag_key=pl.col("tags").str.split(":").get(0),
            tag_value=pl.col("tags").str.split(":").get(1),
        )
        .collect()
        .pivot(
            index=cs.all().exclude("tag_value", "tag_key", "tags"),
            values="tag_value",
            columns="tag_key",
        )
        # Select and normalize column names
        .select(
            [
                pl.col("id").alias("rule_id"),
                pl.col("name").alias("rule_name"),
                pl.col("source").alias("log_source"),
                pl.col("tactic"),
                pl.col("technique"),
                pl.col("queries"),
                pl.col("options"),
                pl.col("cases"),
                pl.col("message"),
                pl.col("isDefault").alias("is_default"),
                pl.col("isEnabled").alias("is_enabled"),
                pl.col("isDeleted").alias("is_deleted"),
            ]
        )
        .sort(["source", "tactic", "technique"])
        .to_dicts()
    )
    return detection_rules
