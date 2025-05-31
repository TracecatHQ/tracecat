import contextlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx


def cookies_path() -> Path:
    return Path.home() / ".tracecat_cli_config.json"


@dataclass
class Workspace:
    id: str
    name: str


class LocalCredentialsManager:
    def __init__(self, path: Path) -> None:
        self.path = path

    def __repr__(self) -> str:
        data = self._read_config()
        return f"LocalCredentialsManager(path={self.path}, data={data})"

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

    def get_workspace(self) -> Workspace | None:
        """Get the workspace from config."""
        cfg = self._read_config()
        if ws := cfg.get("workspace"):
            return Workspace(**ws)
        return None

    def _read_config(self) -> dict[str, Any]:
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


manager = LocalCredentialsManager(path=cookies_path())


@contextlib.asynccontextmanager
async def get_client():
    url = os.getenv("TRACECAT_API_URL", "http://localhost/api")
    # Reaad cookie auth
    cookies = manager.read_cookies()
    workspace = manager.get_workspace()
    params = {}
    if workspace:
        params["workspace_id"] = workspace.id
    async with httpx.AsyncClient(
        base_url=url, cookies=cookies, params=params
    ) as client:
        yield client


def validate_wf_defn_yaml_path(definition_path: str) -> Path:
    """Validate a workflow definition YAML file."""
    path = Path(definition_path)
    if not path.exists():
        raise ValueError(f"File {path} does not exist")
    if not path.is_file():
        raise ValueError(f"File {path} is not a file")
    if path.suffix not in (".yaml", ".yml"):
        raise ValueError(f"File {path} is not a .yaml or .yml file")
    return path
