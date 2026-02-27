from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import SecretStr

type MCPServerType = Literal["http", "stdio"]


@dataclass(slots=True)
class TokenResponse:
    """Data class for OAuth token responses."""

    access_token: SecretStr
    refresh_token: SecretStr | None = None
    expires_in: int = 3600
    scope: str = ""
    token_type: str = "Bearer"
