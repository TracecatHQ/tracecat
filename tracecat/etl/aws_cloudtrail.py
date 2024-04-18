"""ETL functions for AWS CloudTrail logs (version 1.10).

API reference: https://docs.aws.amazon.com/awscloudtrail/latest/userguide/cloudtrail-event-reference-record-contents.html
"""

import gzip
import io
from datetime import datetime, timedelta
from functools import partial
from itertools import chain
from pathlib import Path
from uuid import uuid4

import boto3
import botocore.session
import orjson
import polars as pl
from tqdm.contrib.concurrent import thread_map

from tracecat.config import TRACECAT__TRIAGE_DIR
from tracecat.etl.aws_s3 import list_objects_under_prefix
from tracecat.logger import standard_logger

logger = standard_logger("runner.aws_cloudtrail")


AWS_CLOUDTRAIL__TRIAGE_DIR = TRACECAT__TRIAGE_DIR / "aws_cloudtrail"
AWS_CLOUDTRAIL__S3_PREFIX_FORMAT = (
    "AWSLogs/{account_id}/CloudTrail/{region}/{year}/{month:02d}/{day:02d}/"
)

AWS_CLOUDTRAIL__SELECTED_FIELDS = [
    "userIdentity",
    "userAgent",
    "sourceIPAddress",
    "eventTime",
    "eventName",
    "eventSource",
    "requestParameters",
    "responseElements",
    "awsRegion",
]

AWS_CLOUDTRAIL__JSON_FIELDS = [
    "userIdentity",
    "requestParameters",
    "responseElements",
]


def _get_aws_regions() -> list[str]:
    session = botocore.session.get_session()
    available_regions = session.get_available_regions("ec2")
    return available_regions


def _list_cloudtrail_objects_under_prefix(
    bucket_name: str,
    account_id: str,
    date_range: pl.Series,
    regions: list[str],
) -> list[str]:
    nested_object_names = []
    for region in regions:
        # List all relevant prefixes given dates in date range
        prefixes = iter(
            AWS_CLOUDTRAIL__S3_PREFIX_FORMAT.format(
                account_id=account_id,
                region=region,
                year=dt.year,
                month=dt.month,
                day=dt.day,
            )
            for dt in date_range
        )
        # List all object names that start with prefixes
        region_object_names = thread_map(
            partial(list_objects_under_prefix, bucket_name=bucket_name),
            prefixes,
            desc="📂 Enumerate AWS CloudTrail logs",
        )
        nested_object_names.extend(region_object_names)
    object_names = list(chain.from_iterable(nested_object_names))
    return object_names


def _record_to_json(record: dict, json_fields: list[str]) -> dict:
    normalized_record = {}
    for k, v in record.items():
        if k in json_fields:
            normalized_record[k] = orjson.dumps(v).decode()
        else:
            normalized_record[k] = v
    return orjson.dumps(normalized_record)


def _load_cloudtrail_gzip_file(object_name: str, bucket_name: str) -> Path:
    """Load a single AWS CloudTrail log file from S3 and save as ndjson."""
    # Download using boto3
    buffer = io.BytesIO()
    client = boto3.client("s3")
    client.download_fileobj(bucket_name, object_name, buffer)
    # Unzip and save as ndjson
    buffer.seek(0)
    with gzip.GzipFile(fileobj=buffer, mode="rb") as f:
        records = orjson.loads(f.read().decode("utf-8"))["Records"]
    # NOTE: We force nested JSONs to be strings
    ndjson_file_path = (AWS_CLOUDTRAIL__TRIAGE_DIR / uuid4().hex).with_suffix(".ndjson")
    with open(ndjson_file_path, "w") as f:
        # Stream each record into an ndjson file
        for record in records:
            log_bytes = _record_to_json(
                record=record, json_fields=AWS_CLOUDTRAIL__SELECTED_FIELDS
            )
            f.write(log_bytes.decode("utf-8") + "\n")
    return ndjson_file_path


def _load_cloudtrail_gzip_files(
    object_names: list[str], bucket_name: str
) -> list[Path]:
    """Load multiple AWS CloudTrail log files from S3 and save as ndjson."""
    ndjson_file_paths = thread_map(
        partial(_load_cloudtrail_gzip_file, bucket_name=bucket_name),
        object_names,
        desc="📂 Download AWS CloudTrail logs",
    )
    return ndjson_file_paths


def _load_cloudtrail_ndjson_files(ndjson_file_paths: list[Path]) -> list[dict]:
    logger.info("📂 Convert and filter triaged AWS CloudTrail logs")
    logs = (
        # NOTE: This might cause memory to blow up
        pl.scan_ndjson(ndjson_file_paths, infer_schema_length=None)
        .select(AWS_CLOUDTRAIL__SELECTED_FIELDS)
        # Defensive to avoid concats with mismatched struct column schemas
        .select(pl.all().cast(pl.Utf8))
        .collect(streaming=True)
        .to_dicts()
    )
    return logs


def load_cloudtrail_logs(
    account_id: str,
    bucket_name: str,
    start: datetime,
    end: datetime,
    regions: list[str] | None = None,
) -> list[dict]:
    regions = regions or _get_aws_regions()
    logger.info(
        "📂 Download AWS CloudTrail logs from: account_id=%s, regions=%s",
        account_id,
        regions,
    )
    date_range = pl.date_range(
        start=start.date(),
        end=end.date(),
        interval=timedelta(days=1),
        eager=True,
    )
    object_names = _list_cloudtrail_objects_under_prefix(
        bucket_name=bucket_name,
        account_id=account_id,
        date_range=date_range,
        regions=regions,
    )
    ndjson_file_paths = _load_cloudtrail_gzip_files(
        object_names, bucket_name=bucket_name
    )
    cloudtrail_logs = _load_cloudtrail_ndjson_files(ndjson_file_paths)
    return cloudtrail_logs
