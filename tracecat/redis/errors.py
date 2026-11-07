"""Errors raised by Redis IAM logins."""

from redis.exceptions import ConnectionError


class RedisIamAuthUnavailableError(ConnectionError):
    """The Redis handshake kept failing with a non-credential server error.

    ElastiCache answers ``AUTH`` with ``IAM Authentication service is not
    available.`` while IAM is briefly unavailable or a policy change is
    propagating. That is not a rejected credential, so it is raised as a
    ``ConnectionError`` and callers treat it like any other dropped connection.
    """


class RedisCredentialUnavailableError(ConnectionError):
    """The cloud credential for a Redis login could not be obtained.

    For example STS or Entra ID is briefly unreachable. Raised as a
    ``ConnectionError`` so callers retry it like a dropped connection, and so
    redis-py closes the half-opened socket instead of pooling it unauthenticated.
    """
