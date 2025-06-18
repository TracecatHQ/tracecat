from abc import ABC, abstractmethod
from typing import ClassVar

from tracecat import config
from tracecat.integrations.models import TokenResponse


class BaseOauthProvider(ABC):
    id: ClassVar[str]

    @property
    def base_url(self) -> str:
        return f"{config.TRACECAT__PUBLIC_APP_URL}/integrations/{self.id}"

    @property
    def redirect_uri(self) -> str:
        """The redirect URI for the OAuth provider."""
        return f"{self.base_url}/callback"

    @abstractmethod
    async def get_authorization_url(self, state: str) -> str:
        pass

    @abstractmethod
    async def exchange_code_for_token(self, code: str, state: str) -> TokenResponse:
        pass
