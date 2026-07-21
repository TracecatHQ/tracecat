"""Shared Redis connection configuration."""

from typing import TypedDict
from urllib.parse import urlparse

from tracecat.config import REDIS_SSL_CA_DATA


class RedisTLSKwargs(TypedDict, total=False):
    ssl_ca_data: str


def redis_tls_kwargs(url: str) -> RedisTLSKwargs:
    """Return TLS keyword arguments shared by all Redis clients."""
    if not REDIS_SSL_CA_DATA:
        return {}
    if urlparse(url).scheme != "rediss":
        raise ValueError("REDIS_SSL_CA_DATA requires a rediss:// Redis URL")
    return {"ssl_ca_data": REDIS_SSL_CA_DATA}
