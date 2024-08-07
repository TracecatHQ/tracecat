import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv())


@dataclass
class Role:
    type: str
    user_id: uuid.UUID | None
    service_id: str


# In reality we should use the user's id from config.toml
@dataclass(frozen=True)
class Config:
    role: Role = field(
        default_factory=lambda: Role(
            type="service", user_id=uuid.UUID(int=0), service_id="tracecat-cli"
        )
    )
    jwt_token: str = field(default="super-secret-jwt-token")
    docs_path: Path = field(default_factory=lambda: Path("docs"))
    docs_api_group: str = field(default="API Documentation")
    docs_api_pages_group: str = field(default="Reference")
    api_url: str = field(
        default=os.getenv("TRACECAT__PUBLIC_API_URL", "http://localhost/api")
    )
    cookies_path: Path = field(default=Path.home() / ".tracecat_cookies.json")


config = Config()
