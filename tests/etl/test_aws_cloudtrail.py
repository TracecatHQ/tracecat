import glob
import gzip
import hashlib
import io
import logging
import os
from datetime import datetime

import orjson
import polars as pl
import pytest
from minio import Minio
from polars.testing import assert_frame_equal
from tqdm.contrib.concurrent import thread_map

from tracecat.etl.aws_cloudtrail import (
    load_cloudtrail_logs,
)

TEST__AWS_CLOUDTRAIL__S3_PREFIX_FORMAT = (
    "AWSLogs/o-123xyz1234/111222333444/CloudTrail/{region}/{year}/{month:02d}/{day:02d}"
)
TEST__START_DATETIME = datetime(2023, 1, 1)
TEST__DATES_TO_RECORDS_RATIO = 3


# AWS CloudTrail log samples


@pytest.fixture(scope="session")
def cloudtrail_records() -> list[dict]:
    # Parse all sample logs into list of records
    records = []
    for path in glob.iglob("tests/data/log_samples/aws_cloudtrail/*.json"):
        with open(path) as f:
            sample_records = orjson.loads(f.read())["Records"]
        records += sample_records
    return records


def put_cloudtrail_logs(records_object_name: tuple[list[dict], str]) -> int:
    records, object_name = records_object_name
    client = Minio(
        os.environ["MINIO_ENDPOINT"],
        access_key=os.environ["MINIO_ACCESS_KEY"],
        secret_key=os.environ["MINIO_SECRET_KEY"],
        secure=False,
    )
    logs_data = orjson.dumps({"Records": records})
    obj_data = gzip.compress(logs_data)
    obj_size = len(obj_data)
    client.put_object(
        bucket_name=os.environ["AWS_CLOUDTRAIL__BUCKET_NAME"],
        object_name=object_name,
        data=io.BytesIO(obj_data),
        length=obj_size,
    )
    # # Check if the upload was successful
    # assert client.stat_object(AWS_CLOUDTRAIL__BUCKET_NAME, object_name)
    return obj_size


@pytest.fixture(
    scope="session",
    params=[1000],
)
def cloudtrail_log_files(request, cloudtrail_records) -> pl.DataFrame:
    """Add AWS CloudTrail log files into MinIO and return the expected DataFrame of records."""

    bucket_size = 0
    n_records = request.param
    # Use pigeonhole principle to map each record to a unique timestamp
    timestamps = pl.datetime_range(
        start=TEST__START_DATETIME,
        end=TEST__START_DATETIME
        + pl.duration(minutes=n_records * TEST__DATES_TO_RECORDS_RATIO),
        interval="1m",
    )
    # NOTE: Assume only 3 regions active
    aws_regions = ["us-west-1", "us-west-2", "eu-central-1"]
    records = (
        pl.from_dicts(cloudtrail_records)
        .sample(n_records, with_replacement=True)
        .with_columns(eventTime=timestamps.sample(n_records))
        .sort("eventTime")
        .set_sorted("eventTime")
        .to_struct(name="record")
        .to_frame()
        .with_columns(eventTime=pl.col("record").struct.field("eventTime"))
        # Split records into 15-minute intervals
        .group_by_dynamic("eventTime", every="15m")
        .agg(pl.col("record").alias("records"))
    )
    multi_region_records = pl.concat(
        [records.with_columns(region=pl.lit(region)) for region in aws_regions]
    )

    put_cloudtrail_params = []
    # For testing purposes, just assume each region has the same records
    # NOTE: This loop is can be vectorized
    for row in multi_region_records.to_dicts():
        event_time = row["eventTime"]
        region = row["region"]
        # NOTE: For performance sake, we do NOT normalize record region to match faked region
        records = row["records"]
        # NOTE: AccountID_CloudTrail_RegionName_YYYYMMDDTHHmmZ_UniqueString.FileNameFormat
        # For example: 123456789012_CloudTrail_us-west-2_20230101T0000Z_1a2b3c4d.json.gz
        uuid = hashlib.md5(str(event_time).encode()).hexdigest()[:8]
        prefix = TEST__AWS_CLOUDTRAIL__S3_PREFIX_FORMAT.format(
            region=region,
            year=event_time.year,
            month=event_time.month,
            day=event_time.day,
        )
        file_name = f"{region}_{event_time.strftime('%Y%m%dT%H%MZ')}_{uuid}.json.gz"
        object_name = f"{prefix}/{file_name}"
        put_cloudtrail_params.append((records, object_name))

    # Upload logs into MinIO
    object_sizes = thread_map(
        put_cloudtrail_logs,
        put_cloudtrail_params,
        desc="🪣 Uploading AWS CloudTrail logs into MinIO",
    )
    bucket_size = sum(object_sizes)
    logging.info("✅ AWS CloudTrail bucket size: %.2f bytes", bucket_size)

    return multi_region_records.select(
        "eventTime",
        "records",
    )


@pytest.mark.parametrize(
    "organization_id",
    [None, "o-123xyz1234"],
)
def test_load_cloudtrail_logs(organization_id, cloudtrail_log_files):
    # Set AWS credentials to minio creds
    os.environ["AWS_ACCESS_KEY_ID"] = os.environ["MINIO_ACCESS_KEY"]
    os.environ["AWS_SECRET_ACCESS_KEY"] = os.environ["MINIO_SECRET_KEY"]
    os.environ["AWS_ENDPOINT_URL_S3"] = f'http://{os.environ["MINIO_ENDPOINT"]}'

    expected_logs = cloudtrail_log_files
    logs = load_cloudtrail_logs(
        account_id="111222333444",
        bucket_name=os.environ["AWS_CLOUDTRAIL__BUCKET_NAME"],
        start=TEST__START_DATETIME,
        end=expected_logs.get_column("eventTime").max(),
        organization_id=organization_id,
    )
    # Check loaded logs match up with uploaded logs
    assert_frame_equal(logs, expected_logs)
