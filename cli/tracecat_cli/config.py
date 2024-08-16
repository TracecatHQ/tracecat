import json
import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypedDict

import httpx
from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv())


@dataclass
class Role:
    type: str
    user_id: uuid.UUID | None
    service_id: str


class Workspace(TypedDict):
    id: str
    name: str


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
    config_path: Path = field(default=Path.home() / ".tracecat_cli_config.json")


config = Config()


class ConfigFileManager:
    def __init__(self, path: Path) -> None:
        self.path = path

    def __repr__(self) -> str:
        data = self._read_config()
        return f"ConfigFileManager(path={self.path}, data={data})"

    def write_cookies(self, cookies: httpx.Cookies) -> None:
        """Write cookies to config."""
        cfg = self._read_config()
        cfg["cookies"] = dict(cookies)
        self._write_config(cfg)

    def read_cookies(self) -> httpx.Cookies:
        """Read cookies from config."""
        cfg = self._read_config()
        return httpx.Cookies(cfg.get("cookies", {}))

    def delete_cookies(self) -> None:
        """Delete cookies from config."""
        cfg = self._read_config()
        cfg.pop("cookies", None)
        self._write_config(cfg)

    def set_workspace(self, workspace_id: uuid.UUID, workspace_name: str) -> Workspace:
        """Set the workspace ID in the configuration."""
        cfg = self._read_config()
        workspace = Workspace(id=str(workspace_id), name=workspace_name)
        cfg["workspace"] = workspace
        self._write_config(cfg)
        return workspace

    def get_workspace(self) -> Workspace | None:
        """Get the workspace ID from the configuration."""
        cfg = self._read_config()
        workspace = cfg.get("workspace")
        return Workspace(**workspace) if workspace else None

    def reset_workspace(self) -> None:
        """Remove the workspace ID from the configuration."""
        cfg = self._read_config()
        cfg.pop("workspace", None)
        self._write_config(cfg)

    def _read_config(self) -> dict[str, str]:
        """Read configuration from file."""
        try:
            with self.path.open() as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _write_config(self, config: dict[str, str]) -> None:
        """Write configuration to file."""
        with self.path.open(mode="w") as f:
            json.dump(config, f, indent=2)


manager = ConfigFileManager(path=config.config_path)
