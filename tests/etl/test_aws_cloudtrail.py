import glob
import gzip
import hashlib
import io
import logging
import os
import shutil
import subprocess
import time
from copy import deepcopy
from datetime import datetime, timedelta

import orjson
import polars as pl
import pytest
from minio import Minio
from polars.testing import assert_frame_equal
from tqdm.contrib.concurrent import thread_map

from tracecat.config import TRACECAT__TRIAGE_DIR
from tracecat.etl.aws_cloudtrail import (
    AWS_CLOUDTRAIL__JSON_FIELDS,
    AWS_CLOUDTRAIL__SELECTED_FIELDS,
    list_cloudtrail_objects_under_prefix,
    load_cloudtrail_logs,
)

TEST__AWS_CLOUDTRAIL__S3_PREFIX_FORMAT = (
    "AWSLogs/o-123xyz1234/111222333444/CloudTrail/{region}/{year}/{month:02d}/{day:02d}"
)
TEST__AWS_CLOUDTRAIL_REGIONS = ["us-west-1", "us-west-2", "eu-central-1"]
TEST__N_RECORDS = 100
TEST__DATES_TO_RECORDS_RATIO = 3


TEST__START_DATETIME = datetime(2023, 1, 1)
TEST__END_DATETIME = TEST__START_DATETIME + timedelta(
    minutes=TEST__N_RECORDS * TEST__DATES_TO_RECORDS_RATIO
)


# MinIO settings
MINIO_CONTAINER_NAME = "minio_test_server"
MINIO_ACCESS_KEY = "admin"
MINIO_SECRET_KEY = "password"
MINIO_PORT = 9000
MINIO_REGION = "us-west-2"


@pytest.fixture(scope="session")
def minio_container():
    # Check if the MinIO container is already running
    existing_containers = subprocess.run(
        [
            "docker",
            "ps",
            "--filter",
            f"name={MINIO_CONTAINER_NAME}",
            "--format",
            "{{.Names}}",
        ],
        capture_output=True,
        text=True,
    )

    container_exists = MINIO_CONTAINER_NAME in existing_containers.stdout.strip()
    logging.info("🐳 MinIO container exists: %r", container_exists)

    if not container_exists:
        # Setup: Start MinIO server
        subprocess.run(
            [
                "docker",
                "run",
                "-d",
                "--rm",
                "--name",
                MINIO_CONTAINER_NAME,
                "-p",
                f"{MINIO_PORT}:{MINIO_PORT}",
                "-e",
                f"MINIO_ACCESS_KEY={MINIO_ACCESS_KEY}",
                "-e",
                f"MINIO_SECRET_KEY={MINIO_SECRET_KEY}",
                "minio/minio",
                "server",
                "/data",
            ],
            check=True,
        )
        # Wait for the server to start
        time.sleep(5)
        logging.info("✅ Created minio container %r", MINIO_CONTAINER_NAME)
    else:
        logging.info("✅ Using existing minio container %r", MINIO_CONTAINER_NAME)

    # Connect to MinIO
    client = Minio(
        f"localhost:{MINIO_PORT}",
        access_key=MINIO_ACCESS_KEY,
        secret_key=MINIO_SECRET_KEY,
        secure=False,
    )

    # Create or connect to AWS CloudTrail bucket
    bucket = os.environ["AWS_CLOUDTRAIL__BUCKET_NAME"]
    if not client.bucket_exists(bucket):
        client.make_bucket(bucket)
        logging.info("✅ Created minio bucket %r", bucket)

    yield
    should_cleanup = os.getenv("MINIO_CLEANUP", "1").lower() in (
        "true",
        "1",
    )
    if not container_exists and should_cleanup:
        logging.info("🧹 Cleaning up minio container %r", MINIO_CONTAINER_NAME)
        subprocess.run(["docker", "stop", MINIO_CONTAINER_NAME], check=True)
    else:
        logging.info(
            "🧹 Skipping cleanup of minio container %r. Set `MINIO_CLEANUP=1` to cleanup.",
            MINIO_CONTAINER_NAME,
        )


@pytest.fixture(scope="session")
def cloudtrail_records() -> dict[str, list[dict]]:
    """Multi-region records"""
    # Parse all sample logs into list of records
    records = []
    for path in glob.iglob("tests/data/log_samples/aws_cloudtrail/*.json"):
        with open(path) as f:
            sample_records = orjson.loads(f.read())["Records"]
        records += sample_records

    # NOTE: Can be vectorized
    new_records = []
    for region in TEST__AWS_CLOUDTRAIL_REGIONS:
        for record in records:
            new_record = deepcopy(record)
            new_record["awsRegion"] = region
            new_records.append(new_record)

    return new_records


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


@pytest.fixture(scope="session")
def cloudtrail_log_files(minio_container, cloudtrail_records):
    """Add AWS CloudTrail log files into MinIO and return the expected DataFrame of records."""

    bucket_size = 0
    n_records = TEST__N_RECORDS
    # Use pigeonhole principle to map each record to a unique timestamp
    timestamps = pl.datetime_range(
        start=TEST__START_DATETIME,
        end=TEST__END_DATETIME,
        interval="1m",
    )
    # NOTE: Assume only 3 regions active
    logs = (
        pl.from_dicts(cloudtrail_records)
        .sample(n_records, with_replacement=True)
        .with_columns(eventTime=timestamps.sample(n_records))
        .sort("eventTime")
        .set_sorted("eventTime")
        .to_struct(name="record")
        .to_frame()
        .with_columns(
            eventTime=pl.col("record").struct.field("eventTime"),
            awsRegion=pl.col("record").struct.field("awsRegion"),
        )
        # Split records into 15-minute intervals
        .group_by_dynamic("eventTime", every="15m", group_by="awsRegion")
        .agg(pl.col("record").alias("records"))
    )

    put_cloudtrail_params = []
    # For testing purposes, just assume each region has the same records
    # NOTE: This loop is can be vectorized
    for row in logs.to_dicts():
        event_time = row["eventTime"]
        region = row["awsRegion"]
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

    yield logs.select("eventTime", "records")

    # Cleanup triage direcotry
    shutil.rmtree(TRACECAT__TRIAGE_DIR)


def test_list_objects_under_prefix_length(cloudtrail_log_files):
    # Set AWS credentials to minio creds
    os.environ["AWS_ACCESS_KEY_ID"] = os.environ["MINIO_ACCESS_KEY"]
    os.environ["AWS_SECRET_ACCESS_KEY"] = os.environ["MINIO_SECRET_KEY"]
    os.environ["AWS_ENDPOINT_URL_S3"] = f'http://{os.environ["MINIO_ENDPOINT"]}'

    # Check number of objects listed is equal to number of files uplaoded
    object_names = list_cloudtrail_objects_under_prefix(
        account_id="111222333444",
        bucket_name=os.environ["AWS_CLOUDTRAIL__BUCKET_NAME"],
        start=TEST__START_DATETIME,
        end=TEST__END_DATETIME,
        organization_id="o-123xyz1234",
    )
    assert len(object_names) == len(cloudtrail_log_files)


def test_load_cloudtrail_logs(cloudtrail_log_files):
    # Set AWS credentials to minio creds
    os.environ["AWS_ACCESS_KEY_ID"] = os.environ["MINIO_ACCESS_KEY"]
    os.environ["AWS_SECRET_ACCESS_KEY"] = os.environ["MINIO_SECRET_KEY"]
    os.environ["AWS_ENDPOINT_URL_S3"] = f'http://{os.environ["MINIO_ENDPOINT"]}'

    expected_logs = (
        cloudtrail_log_files.select("records")
        .explode("records")
        .unnest("records")
        .select(AWS_CLOUDTRAIL__SELECTED_FIELDS)
        .with_columns(
            pl.col(AWS_CLOUDTRAIL__JSON_FIELDS)
            .map_elements(lambda x: orjson.dumps(x).decode(), return_dtype=pl.Utf8)
            .replace(None, "null")
        )
    )
    logs = load_cloudtrail_logs(
        account_id="111222333444",
        bucket_name=os.environ["AWS_CLOUDTRAIL__BUCKET_NAME"],
        start=TEST__START_DATETIME,
        end=TEST__END_DATETIME,
        organization_id="o-123xyz1234",
    ).collect()

    # Check decoded logs match up with uploaded logs
    logs = logs.sort("awsRegion", "eventTime")
    expected_logs = expected_logs.sort("awsRegion", "eventTime")
    try:
        assert_frame_equal(logs, expected_logs)
    except AssertionError:
        # Check first 5 rows of logs and expected logs
        assert_frame_equal(logs.head(5), expected_logs.head(5))
