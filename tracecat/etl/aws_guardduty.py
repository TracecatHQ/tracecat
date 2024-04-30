"""ETL functions for AWS GuardDuty findings.

API reference: https://docs.aws.amazon.com/guardduty/latest/ug/guardduty_finding-types-active.html
"""

import logging
from collections.abc import Generator
from functools import partial
from itertools import chain
from typing import TYPE_CHECKING

import boto3
import botocore.session
import polars as pl
from tqdm.contrib.concurrent import thread_map

from tracecat.logger import standard_logger

if TYPE_CHECKING:
    from mypy_boto3_guardduty.type_defs import GetFindingsResponseTypeDef

logger = standard_logger("runner.aws_guardduty")

# Supress botocore info logs
logging.getLogger("botocore").setLevel(logging.CRITICAL)


def get_aws_regions() -> list[str]:
    session = botocore.session.get_session()
    available_regions = session.get_available_regions("ec2")
    return available_regions


GET_FINDINGS_MAX_CHUNK_SIZE = 50


def list_guardduty_findings(
    regions: list[str] | None = None,
    chunk_size: int = 50,
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

    regions = regions or []

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

    logger.info(f"Retrieved {len(findings)} findings")
    return pl.DataFrame(findings)


GUARDDUTY_DEFAULT_STRUCT_COLS = ["Service", "Resource"]


def stringify_struct_columns(df: pl.DataFrame) -> pl.DataFrame:
    return (
        df.lazy()
        .with_columns(
            pl.col(c).struct.json_encode() for c in GUARDDUTY_DEFAULT_STRUCT_COLS
        )
        .collect(streaming=True)
    )
