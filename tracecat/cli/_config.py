from dataclasses import dataclass, field
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

from tracecat import config
from tracecat.auth.credentials import Role

load_dotenv(find_dotenv())


# In reality we should use the user's id from config.toml
@dataclass(frozen=True)
class Config:
    role: Role = field(
        default_factory=lambda: Role(type="service", user_id="default-tracecat-user")
    )
    jwt_token: str = field(default="super-secret-jwt-token")
    docs_path: Path = field(default_factory=lambda: Path("docs"))
    docs_api_group: str = field(default="API Documentation")
    docs_api_pages_group: str = field(default="Reference")
    api_url: str = field(default=config.TRACECAT__EXTERNAL_API_URL)


config = Config()
