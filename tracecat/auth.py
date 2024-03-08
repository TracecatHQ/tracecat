import hashlib
import os


def compute_hash(object_id: str) -> str:
    return hashlib.sha256(
        f"{object_id}{os.environ["TRACECAT__SIGNING_SECRET"]}".encode()
    ).hexdigest()
