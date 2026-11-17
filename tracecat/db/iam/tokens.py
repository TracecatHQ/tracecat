"""Token providers for Postgres IAM login.

``config.TRACECAT__DB_AUTH`` picks how Tracecat logs in to Postgres:

- ``DbPasswordAuth`` (default): a static password, as before.
- ``DbAwsIamAuth``: RDS IAM tokens (``tracecat.db.iam.aws``).
- ``DbAzureEntraAuth``: Entra ID tokens (``tracecat.db.iam.azure``).

Each cloud module, and its SDK, is imported only when that mode is used (an
exception to the backend import rule), so a deployment only loads its own
cloud's SDK.
"""

from tracecat.db.iam.types import (
    DbAwsIamAuth,
    DbAzureEntraAuth,
    DbTokenAuth,
    DbTokenProvider,
)


def build_token_provider(auth: DbTokenAuth, username: str) -> DbTokenProvider:
    """Build the async password callable asyncpg calls for each new connection.

    Args:
        auth: The token mode.
        username: Database user the token is for (``TRACECAT__DB_USER``).
    """
    match auth:
        case DbAwsIamAuth():
            from tracecat.db.iam.aws import RdsIamTokenProvider

            return RdsIamTokenProvider(auth, username)
        case DbAzureEntraAuth():
            from tracecat.db.iam.azure import AzureEntraTokenProvider

            return AzureEntraTokenProvider(auth)


def mint_token(auth: DbTokenAuth, username: str) -> str:
    """Fetch one token synchronously, for a one-off login such as alembic's.

    Raises:
        DatabaseCredentialUnavailableError: The token could not be obtained.
    """
    match auth:
        case DbAwsIamAuth():
            from tracecat.db.iam.aws import RdsIamTokenProvider

            return RdsIamTokenProvider(auth, username).mint()
        case DbAzureEntraAuth():
            from tracecat.db.iam.azure import mint_entra_token

            return mint_entra_token(auth)
