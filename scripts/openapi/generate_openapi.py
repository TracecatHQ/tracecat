#!/usr/bin/env python3
"""Generate OpenAPI spec from FastAPI app without running server."""

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

from cryptography.fernet import Fernet


def _configure_openapi_env() -> None:
    """Seed deterministic placeholder config for offline schema generation."""
    # OpenAPI generation should not depend on a developer's local .env or any
    # production secrets. These values are fake, process-local placeholders
    # used only so config import and route construction can succeed.
    os.environ["TRACECAT__DB_ENCRYPTION_KEY"] = Fernet.generate_key().decode()
    os.environ["TRACECAT__SERVICE_KEY"] = "openapi-service-key"
    os.environ["TRACECAT__SIGNING_SECRET"] = "openapi-signing-secret"
    os.environ["USER_AUTH_SECRET"] = "openapi-user-auth-secret"
    os.environ["TRACECAT__AUTH_TYPES"] = "basic,oidc"
    os.environ["OIDC_ISSUER"] = "https://issuer.example.com"
    os.environ["OIDC_CLIENT_ID"] = "openapi-client"
    os.environ["OIDC_CLIENT_SECRET"] = "openapi-client-secret"


_configure_openapi_env()

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Import and configure logger before importing the app
from httpx_oauth.oauth2 import BaseOAuth2  # noqa: E402

from tracecat.logger import logger  # noqa: E402

logger.remove()  # Remove all handlers
logger.add(lambda _: None)  # Add a no-op handler to prevent any output


class _OpenAPIOpenIDStub(BaseOAuth2[dict[str, str]]):
    """Stub OIDC client used only for OpenAPI generation.

    `httpx_oauth.clients.openid.OpenID` performs network discovery in `__init__`,
    which makes schema generation depend on a live issuer. For OpenAPI generation
    we only need route construction to succeed.
    """

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        openid_configuration_endpoint: str,
        name: str = "openid",
        base_scopes: list[str] | None = None,
    ) -> None:
        super().__init__(
            client_id,
            client_secret,
            "https://issuer.example.com/oauth/authorize",
            "https://issuer.example.com/oauth/token",
            name=name,
            base_scopes=base_scopes,
        )


def main() -> None:
    """Generate OpenAPI spec and write to stdout or file."""
    with patch("tracecat.auth.oidc.OpenID", _OpenAPIOpenIDStub):
        from tracecat.api.app import app

        openapi_spec = app.openapi()

    if len(sys.argv) > 1:
        # Write to file if path provided
        output_path = Path(sys.argv[1])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(openapi_spec, indent=2))
        print(f"OpenAPI spec written to {output_path}", file=sys.stderr)
    else:
        # Write to stdout
        print(json.dumps(openapi_spec, indent=2))


if __name__ == "__main__":
    main()
