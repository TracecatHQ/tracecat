"""ETL functions for AWS GuardDuty findings.

API reference: https://docs.aws.amazon.com/guardduty/latest/ug/guardduty_finding-types-active.html
"""

import logging
from collections.abc import Generator
from datetime import datetime
from functools import partial
from itertools import chain
from typing import TYPE_CHECKING

import boto3
import diskcache as dc
import mmh3
import polars as pl
from loguru import logger
from tqdm.contrib.concurrent import thread_map

from tracecat.config import TRACECAT__TRIAGE_DIR
from tracecat.contexts import ctx_role

if TYPE_CHECKING:
    from mypy_boto3_guardduty.type_defs import GetFindingsResponseTypeDef


# Supress botocore info logs
logging.getLogger("botocore").setLevel(logging.CRITICAL)

AWS_GUARDDUTY__TRIAGE_DIR = TRACECAT__TRIAGE_DIR / "aws_guardduty"
AWS_GUARDDUTY__TRIAGE_DIR.mkdir(parents=True, exist_ok=True)

GET_FINDINGS_MAX_CHUNK_SIZE = 50


def _get_all_guardduty_findings(
    chunk_size: int = GET_FINDINGS_MAX_CHUNK_SIZE,
) -> pl.DataFrame:
    """Get GuardDuty findings for the specified time range.

    Args:
        region: AWS region
        start_time: ISO 8601 formatted start time
        end_time: ISO 8601 formatted end time
        max_results: Maximum number of findings to return
        severity_threshold: Minimum severity threshold to return

    Returns:
        GuardDuty findings as a Polars DataFrame
    """
    client = boto3.client("guardduty")
    list_findings_paginator = client.get_paginator("list_findings")

    # For all regions and detectors, list findings
    findings: list[GetFindingsResponseTypeDef] = []
    detectors = client.list_detectors()["DetectorIds"]
    chunk_size = min(chunk_size, GET_FINDINGS_MAX_CHUNK_SIZE)

    def chunker(finding_ids: list[str]) -> Generator[list[str], None, None]:
        for i in range(0, len(finding_ids), chunk_size):
            yield finding_ids[i : i + chunk_size]

    def getter(finding_ids: list[str], *, detector_id: str) -> list[str]:
        client = boto3.client("guardduty")
        findings = client.get_findings(DetectorId=detector_id, FindingIds=finding_ids)
        return findings.get("Findings", [])

    for detector_id in detectors:
        finding_ids: list[str] = []
        # TODO: Parallelize this?
        for page in list_findings_paginator.paginate(DetectorId=detector_id):
            finding_ids.extend(page.get("FindingIds", []))
        logger.info(f"Found {len(finding_ids)} findings in detector {detector_id}")

        detector_findings: list[list[str]] = thread_map(
            partial(getter, detector_id=detector_id),
            chunker(finding_ids=finding_ids),
            desc="📂 Getting AWS GuardDuty findings",
        )
        findings.extend(chain.from_iterable(detector_findings))

    logger.info(f"Retrieved {len(findings)} GuardDuty findings")
    df = pl.DataFrame(findings)
    return df


GUARDDUTY_DEFAULT_STRUCT_COLS = ["Service", "Resource"]


def _stringify_struct_columns(df: pl.DataFrame | pl.LazyFrame) -> pl.LazyFrame:
    return df.lazy().with_columns(
        pl.col(c).struct.json_encode() for c in GUARDDUTY_DEFAULT_STRUCT_COLS
    )


def load_guardduty_findings(
    start: datetime,
    end: datetime,
    account_id: str,
    organization_id: str,
) -> pl.LazyFrame:
    """Load AWS GuardDuty findings for the specified time range.

    Caches and reads from disk to avoid repeated (expensive) API calls.

    Args:
        regions: AWS regions to load findings from
        chunk_size: Maximum number of findings to load per request

    Returns:
        GuardDuty findings as a Polars DataFrame
    """
    # Include the session role in the cache key to avoid collisions
    # when possibly serving multiple users concurrently
    role = ctx_role.get()
    logger.info(f"Loading GuardDuty findings for role {role}")

    key = mmh3.hash(
        f"{role}:{start}{end}{account_id}{organization_id}".encode(), seed=42
    )

    df: pl.DataFrame
    dt_col = "CreatedAt"
    with dc.Cache(directory=AWS_GUARDDUTY__TRIAGE_DIR) as cache:
        if key in cache:
            logger.info("Cache hit for GuardDuty findings")
            # Structs here are already stringified
            df = cache[key]
        else:
            logger.info("Cache miss for GuardDuty findings")
            df = (
                _get_all_guardduty_findings()
                .lazy()
                .pipe(_stringify_struct_columns)
                .collect(streaming=True)
            )
            # Cache for 10 minutes
            cache.set(key=key, value=df, expire=600)
        # Apply time range filter
        df = df.filter(pl.col(dt_col).is_between(start, end))
        return df.lazy()
