"""The sanitized error a run raises when an externally backed secret fails."""

from __future__ import annotations

from tracecat.exceptions import TracecatCredentialsError
from tracecat.secrets.enums import SecretResolutionErrorCode


class SecretResolutionError(TracecatCredentialsError):
    """Sanitized failure while resolving an externally backed workspace secret."""

    def __init__(
        self,
        code: SecretResolutionErrorCode,
        *,
        alias: str,
        environment: str,
        provider_label: str,
        vendor_code: str | None = None,
        vendor_code_field: str = "provider_error_code",
    ) -> None:
        self.code = code
        self.alias = alias
        self.environment = environment
        self.vendor_code = vendor_code
        self.vendor_code_field = vendor_code_field
        suffix = f" ({provider_label} error code {vendor_code})" if vendor_code else ""
        super().__init__(
            f"Failed to resolve {provider_label}-backed secret {alias!r} in "
            f"environment {environment!r}: {code.value}{suffix}",
            detail={
                "secret_name": alias,
                "environment": environment,
                "error_code": code.value,
                vendor_code_field: vendor_code,
            },
        )
