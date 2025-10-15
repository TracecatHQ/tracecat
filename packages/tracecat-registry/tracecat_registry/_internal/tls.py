"""TLS helper utilities for registry actions."""

from __future__ import annotations

import os
import tempfile
from typing import Protocol


class _SupportsClose(Protocol):
    def close(self) -> None: ...


class TemporaryClientCertificate:
    """Manage temporary files for SSL client certificates and keys."""

    def __init__(
        self,
        *,
        client_cert_str: str | None = None,
        client_key_str: str | None = None,
        client_key_password: str | None = None,
    ) -> None:
        self.client_cert_str = client_cert_str
        self.client_key_str = client_key_str
        self.client_key_password = client_key_password
        self._temp_files: list[tuple[_SupportsClose, str]] = []

    def __enter__(
        self,
    ) -> str | tuple[str, str] | tuple[str, str, str] | None:
        cert_path: str | None = None
        key_path: str | None = None

        if self.client_cert_str:
            cert_file = tempfile.NamedTemporaryFile(
                mode="w", delete=False, encoding="utf-8"
            )
            self._temp_files.append((cert_file, cert_file.name))
            cert_file.write(self.client_cert_str)
            cert_file.flush()
            cert_path = cert_file.name

        if self.client_key_str:
            key_file = tempfile.NamedTemporaryFile(
                mode="w", delete=False, encoding="utf-8"
            )
            self._temp_files.append((key_file, key_file.name))
            key_file.write(self.client_key_str)
            key_file.flush()
            key_path = key_file.name

        if cert_path and key_path:
            if self.client_key_password:
                return (cert_path, key_path, self.client_key_password)
            return (cert_path, key_path)
        if cert_path:
            return cert_path
        return None

    def __exit__(self, *args: object) -> None:
        for temp_file, path in self._temp_files:
            temp_file.close()
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
        self._temp_files.clear()
