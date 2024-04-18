from itertools import chain

import boto3


def list_objects_under_prefix(prefix: str, bucket_name: str) -> list[str]:
    client = boto3.client("s3")
    paginator = client.get_paginator("list_objects_v2")
    pages = paginator.paginate(Bucket=bucket_name, Prefix=prefix)
    object_names = []
    try:
        object_names = [
            content["Key"]
            for content in chain.from_iterable(p["Contents"] for p in pages)
        ]
    except KeyError as err:
        if "Contents" not in str(err):
            raise err
    return object_names
