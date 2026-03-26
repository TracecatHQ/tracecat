from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import grpc
from pwdlib.hashers.bcrypt import BcryptHasher

from tracecat import config
from tracecat.auth.dex.mode import MCPDexMode, get_mcp_dex_mode

from . import api_pb2, api_pb2_grpc


class DexLocalAuthProvisioningError(RuntimeError):
    """Dex local-auth provisioning failed."""


_DEX_PASSWORD_HASHER = BcryptHasher(rounds=12, prefix="2b")


class DexPasswordStub(Protocol):
    async def CreatePassword(
        self,
        request: api_pb2.CreatePasswordReq,
        *,
        timeout: float | None = None,
    ) -> api_pb2.CreatePasswordResp: ...

    async def UpdatePassword(
        self,
        request: api_pb2.UpdatePasswordReq,
        *,
        timeout: float | None = None,
    ) -> api_pb2.UpdatePasswordResp: ...

    async def DeletePassword(
        self,
        request: api_pb2.DeletePasswordReq,
        *,
        timeout: float | None = None,
    ) -> api_pb2.DeletePasswordResp: ...


@dataclass
class DexLocalAuthProvisioningService:
    target: str
    timeout_seconds: float

    def __post_init__(self) -> None:
        self._channel: grpc.aio.Channel | None = None
        self._stub: DexPasswordStub | None = None

    def _get_stub(self) -> DexPasswordStub:
        if self._stub is None:
            self._channel = grpc.aio.insecure_channel(self.target)
            self._stub = api_pb2_grpc.DexStub(self._channel)
        return self._stub

    def _build_password(
        self,
        *,
        email: str,
        password_hash: str,
        username: str,
        user_id: str,
    ) -> api_pb2.Password:
        return api_pb2.Password(
            email=email,
            hash=password_hash.encode(),
            username=username,
            user_id=user_id,
        )

    async def _create_password(
        self,
        *,
        email: str,
        password_hash: str,
        username: str,
        user_id: str,
        timeout: float | None = None,
    ) -> api_pb2.CreatePasswordResp:
        return await self._get_stub().CreatePassword(
            api_pb2.CreatePasswordReq(
                password=self._build_password(
                    email=email,
                    password_hash=password_hash,
                    username=username,
                    user_id=user_id,
                )
            ),
            timeout=timeout if timeout is not None else self.timeout_seconds,
        )

    async def is_local_auth_enabled(self) -> bool:
        return get_mcp_dex_mode() is MCPDexMode.BASIC

    async def upsert_password(
        self,
        *,
        email: str,
        password_hash: str,
        username: str,
        user_id: str,
        previous_email: str | None = None,
    ) -> None:
        if not await self.is_local_auth_enabled():
            return
        if previous_email and previous_email != email:
            await self._create_or_replace_password(
                email=email,
                password_hash=password_hash,
                username=username,
                user_id=user_id,
                replace_on_conflict=True,
            )
            try:
                await self.delete_password(previous_email)
            except DexLocalAuthProvisioningError as exc:
                try:
                    await self.delete_password(email)
                except DexLocalAuthProvisioningError as rollback_exc:
                    raise DexLocalAuthProvisioningError(
                        "Failed to remove previous Dex password for "
                        f"{previous_email} after creating {email}, and failed "
                        "to roll back the new Dex password"
                    ) from rollback_exc
                raise DexLocalAuthProvisioningError(
                    "Failed to remove previous Dex password for "
                    f"{previous_email} after creating {email}; rolled back the "
                    "new Dex password"
                ) from exc
            return

        try:
            response = await self._create_password(
                email=email,
                password_hash=password_hash,
                username=username,
                user_id=user_id,
            )
        except grpc.aio.AioRpcError as exc:
            raise DexLocalAuthProvisioningError(
                f"Failed to create Dex password for {email}"
            ) from exc

        if not response.already_exists:
            return

        try:
            update_response = await self._get_stub().UpdatePassword(
                api_pb2.UpdatePasswordReq(
                    email=email,
                    new_hash=password_hash.encode(),
                    new_username=username,
                ),
                timeout=self.timeout_seconds,
            )
        except grpc.aio.AioRpcError as exc:
            raise DexLocalAuthProvisioningError(
                f"Failed to update Dex password for {email}"
            ) from exc

        if update_response.not_found:
            await self._create_or_replace_password(
                email=email,
                password_hash=password_hash,
                username=username,
                user_id=user_id,
                replace_on_conflict=False,
            )

    async def delete_password(self, email: str) -> None:
        if not await self.is_local_auth_enabled():
            return
        try:
            await self._get_stub().DeletePassword(
                api_pb2.DeletePasswordReq(email=email),
                timeout=self.timeout_seconds,
            )
        except grpc.aio.AioRpcError as exc:
            raise DexLocalAuthProvisioningError(
                f"Failed to delete Dex password for {email}"
            ) from exc

    async def _create_or_replace_password(
        self,
        *,
        email: str,
        password_hash: str,
        username: str,
        user_id: str,
        replace_on_conflict: bool,
    ) -> None:
        try:
            response = await self._create_password(
                email=email,
                password_hash=password_hash,
                username=username,
                user_id=user_id,
            )
        except grpc.aio.AioRpcError as exc:
            raise DexLocalAuthProvisioningError(
                f"Failed to create Dex password for {email}"
            ) from exc

        if not response.already_exists or not replace_on_conflict:
            return

        await self.delete_password(email)

        try:
            retry_response = await self._create_password(
                email=email,
                password_hash=password_hash,
                username=username,
                user_id=user_id,
            )
        except grpc.aio.AioRpcError as exc:
            raise DexLocalAuthProvisioningError(
                f"Failed to recreate Dex password for {email}"
            ) from exc

        if retry_response.already_exists:
            raise DexLocalAuthProvisioningError(
                f"Dex password for {email} remained conflicted after replacement"
            )


_dex_local_auth_service: DexLocalAuthProvisioningService | None = None


def get_dex_local_auth_service() -> DexLocalAuthProvisioningService | None:
    global _dex_local_auth_service

    if not config.MCP_DEX_GRPC_TARGET:
        return None
    if _dex_local_auth_service is None:
        _dex_local_auth_service = DexLocalAuthProvisioningService(
            target=config.MCP_DEX_GRPC_TARGET,
            timeout_seconds=config.MCP_DEX_GRPC_TIMEOUT_SECONDS,
        )
    return _dex_local_auth_service


def hash_password_for_dex(password: str) -> str:
    return _DEX_PASSWORD_HASHER.hash(password)
