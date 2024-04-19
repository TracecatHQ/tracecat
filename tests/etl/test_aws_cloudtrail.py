import glob
import gzip
import hashlib
import io
import random
from datetime import datetime

import orjson
import polars as pl
import pytest
from minio import Minio
from polars.testing import assert_frame_equal
from tqdm.contrib.concurrent import thread_map

from tests.conftest import (
    AWS_CLOUDTRAIL__BUCKET_NAME,
    MINIO_ACCESS_KEY,
    MINIO_PORT,
    MINIO_SECRET_KEY,
)
from tracecat.etl.aws_cloudtrail import get_aws_regions, load_cloudtrail_logs
from tracecat.logger import standard_logger

logger = standard_logger("tests")


AWS_CLOUDTRAIL__DIR_PATH = "AWSLogs/o-123xyz1234/111222333444/CloudTrail"
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


def put_cloudtrail_logs(logs_sample: list[dict], file_name: str) -> int:
    client = Minio(
        f"localhost:{MINIO_PORT}",
        access_key=MINIO_ACCESS_KEY,
        secret_key=MINIO_SECRET_KEY,
        secure=False,
    )
    logs_data = orjson.dumps({"Records": logs_sample})
    obj_data = gzip.compress(logs_data)
    obj_size = len(obj_data)
    client.put_object(
        bucket_name=AWS_CLOUDTRAIL__BUCKET_NAME,
        object_name=file_name,
        data=io.BytesIO(obj_data),
        length=obj_size,
    )
    # # Check if the upload was successful
    # assert client.stat_object(AWS_CLOUDTRAIL__BUCKET_NAME, file_name)
    return obj_size


@pytest.fixture(
    scope="session",
    params=[(1000, 3)],
    ids=lambda n: f"n_records={n[0]},n_regions={n[1]}",
)
def cloudtrail_log_files(request, cloudtrail_records) -> pl.DataFrame:
    """Add AWS CloudTrail log files into MinIO and return the expected DataFrame of records."""

    bucket_size = 0
    n_records, n_regions = request.param
    # Use pigeonhole principle to map each record to a unique timestamp
    timestamps = pl.date_range(
        start=TEST__START_DATETIME,
        end=TEST__START_DATETIME
        + pl.duration(minutes=n_records * TEST__DATES_TO_RECORDS_RATIO),
        interval="1m",
    )
    aws_regions = random.choices(get_aws_regions(), k=n_regions)
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
        .agg(pl.col("record"))
        .with_columns(uuid=pl.col)
    )
    multi_region_records = pl.concat(
        [records.with_columns(region=pl.lit(region)) for region in aws_regions]
    )

    put_cloudtrail_params = []
    # For testing purposes, just assume each region has the same records
    # NOTE: This loop is can be vectorized
    for record in multi_region_records.to_dicts(drop_null_fields=False):
        event_time = record["eventTime"]
        record = record["record"]
        region = record["region"]
        record["awsRegion"] = region
        # NOTE: AccountID_CloudTrail_RegionName_YYYYMMDDTHHmmZ_UniqueString.FileNameFormat
        # For example: 123456789012_CloudTrail_us-west-2_20230101T0000Z_1a2b3c4d.json.gz
        uuid = hashlib.md5(str(record).encode()).hexdigest()[:8]
        object_name = f"{AWS_CLOUDTRAIL__DIR_PATH}/{region}_{event_time.strftime('%Y%m%dT%H%MZ')}_{uuid}.json.gz"
        put_cloudtrail_params.append((record, object_name))

    # Upload logs into MinIO
    object_sizes = thread_map(
        put_cloudtrail_logs,
        put_cloudtrail_params,
        desc="🪣 Uploading AWS CloudTrail logs into MinIO",
    )
    bucket_size = sum(object_sizes)

    # Check size of bucket
    logger.info("✅ AWS CloudTrail bucket size: %.2f gb", bucket_size / 2**30)

    return multi_region_records.select(
        "eventTime",
        "record",
    )


@pytest.mark.parametrize(
    "organization_id",
    [None, "o-123xyz1234"],
)
def test_load_cloudtrail_logs(organization_id, cloudtrail_log_files):
    expected_logs = cloudtrail_log_files
    logs = load_cloudtrail_logs(
        account_id="111222333444",
        bucket_name=AWS_CLOUDTRAIL__BUCKET_NAME,
        start=TEST__START_DATETIME,
        end=expected_logs.get_column("eventTime").max(),
        organization_id=organization_id,
    )
    # Check loaded logs match up with uploaded logs
    assert_frame_equal(logs, expected_logs)
