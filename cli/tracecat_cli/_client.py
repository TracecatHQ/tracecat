import json
from typing import Any

import httpx
import rich
import typer

from ._config import config
from ._utils import read_cookies


def _aclient():
    return httpx.AsyncClient(
        base_url=config.api_url, cookies=read_cookies(config.cookies_path)
    )


def _client():
    return httpx.Client(
        base_url=config.api_url, cookies=read_cookies(config.cookies_path)
    )


class Client:
    """Tracecat API client."""

    def __init__(self):
        self.client = None
        self.aclient = None

    def __enter__(self, *args, **kwargs):
        self.client = _client()
        return self.client.__enter__(*args, **kwargs)

    def __exit__(self, exc_type, exc_value, traceback):
        if self.client:
            self.client.__exit__(exc_type, exc_value, traceback)

    async def __aenter__(self, *args, **kwargs):
        self.aclient = _aclient()
        return await self.aclient.__aenter__(*args, **kwargs)

    async def __aexit__(self, exc_type, exc_value, traceback):
        if self.aclient:
            await self.aclient.__aexit__(exc_type, exc_value, traceback)

    @staticmethod
    def handle_response(res: httpx.Response) -> Any | None:
        """Handle API responses.

        1. Checks for specific status codes and raises exceptions.
        2. Raise for status
        3. Try to decode JSON response
        4. If 3 fails, return the response text.
        """
        if res.status_code == 422:
            # Unprocessable entity
            rich.print("[red]Data validation error[/red]")
            rich.print(res.json())
            raise typer.Exit()
        if res.status_code == 204:
            # No content
            return None
        res.raise_for_status()
        try:
            return res.json()
        except json.JSONDecodeError as e:
            rich.print(f"[yellow]Couldn't decode JSON response! {e}[/yellow]")
            return res.text
