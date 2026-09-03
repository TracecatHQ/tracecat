"""Typed accessors for required platform secrets.

Each function reads the config value and raises ValueError if empty/unset.
Use these at the point of use instead of reading config.* directly.
"""

from tracecat import config


def get_user_auth_secret() -> str:
    """Get USER_AUTH_SECRET. Raises ValueError if not set."""
    if not config.USER_AUTH_SECRET:
        raise ValueError("USER_AUTH_SECRET is not set")
    return config.USER_AUTH_SECRET


def get_db_encryption_key() -> str:
    """Get TRACECAT__DB_ENCRYPTION_KEY. Raises ValueError if not set."""
    if not config.TRACECAT__DB_ENCRYPTION_KEY:
        raise ValueError("TRACECAT__DB_ENCRYPTION_KEY is not set")
    return config.TRACECAT__DB_ENCRYPTION_KEY


def get_signing_secret() -> str:
    """Get TRACECAT__SIGNING_SECRET. Raises ValueError if not set."""
    if not config.TRACECAT__SIGNING_SECRET:
        raise ValueError("TRACECAT__SIGNING_SECRET is not set")
    return config.TRACECAT__SIGNING_SECRET


def get_service_key() -> str:
    """Get TRACECAT__SERVICE_KEY. Raises ValueError if not set."""
    if not config.TRACECAT__SERVICE_KEY:
        raise ValueError("TRACECAT__SERVICE_KEY is not set")
    return config.TRACECAT__SERVICE_KEY
