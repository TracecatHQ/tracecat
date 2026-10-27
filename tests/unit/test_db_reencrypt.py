"""Re-encrypting stored values across tables during algorithm switches and key rotation."""

import os
import uuid

import orjson
import pytest
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import LargeBinary, select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat import config
from tracecat.agent.channels.service import ENCRYPTED_CONFIG_VALUE_PREFIX
from tracecat.config import DBEncryptionAlgorithm
from tracecat.db.models import (
    AgentChannelToken,
    AgentPreset,
    Base,
    OrganizationSetting,
    PlatformSetting,
    Secret,
    Workspace,
)
from tracecat.db.rls import set_rls_context
from tracecat.secrets.encryption import decrypt_token, encrypt_value, is_current
from tracecat.secrets.reencrypt import (
    ENCRYPTED_COLUMNS,
    ENCRYPTED_JSON_FIELDS,
    UndecryptableValuesError,
    reencrypt_database,
)
from tracecat.secrets.types import ReencryptReport

pytestmark = pytest.mark.usefixtures("db", "env_sandbox")

# Binary columns that hold no DB-encrypted values. A new binary column must be
# added either here or to ENCRYPTED_COLUMNS.
NOT_DB_ENCRYPTED_BINARY_COLUMNS = {
    "webauthn_credential.credential_id",
    "webauthn_credential.public_key",
    "agent_session_history.raw_session_line",
}

FERNET = DBEncryptionAlgorithm.FERNET
AES = DBEncryptionAlgorithm.AES256GCM
Counts = dict[str, tuple[int, int, int, int]]


def test_registry_covers_every_binary_column() -> None:
    """Every LargeBinary column is either re-encrypted or known to hold no ciphertext."""
    binary_columns = {
        f"{table.name}.{column.name}"
        for table in Base.metadata.tables.values()
        for column in table.columns
        if isinstance(column.type, LargeBinary)
    }
    registered = {c.label for c in ENCRYPTED_COLUMNS}

    assert binary_columns == registered | NOT_DB_ENCRYPTED_BINARY_COLUMNS
    assert not registered & NOT_DB_ENCRYPTED_BINARY_COLUMNS
    assert len(registered) == len(ENCRYPTED_COLUMNS)
    assert {t.label for t in ENCRYPTED_JSON_FIELDS} == {"agent_channel_token.config"}


@pytest.fixture
def old_key() -> str:
    """The deployment's key when set, so rows other tests left behind stay decryptable."""
    return (
        os.environ.get("TRACECAT__DB_ENCRYPTION_KEY") or Fernet.generate_key().decode()
    )


@pytest.fixture
def new_key() -> str:
    return Fernet.generate_key().decode()


@pytest.fixture
def seal(monkeypatch: pytest.MonkeyPatch):
    def _seal(plaintext: bytes, *, key: str, algorithm: DBEncryptionAlgorithm) -> bytes:
        with monkeypatch.context() as m:
            m.setattr(config, "TRACECAT__DB_ENCRYPTION_ALGORITHM", algorithm)
            return encrypt_value(plaintext, key=key)

    return _seal


@pytest.fixture
def rotate_to_aes(monkeypatch: pytest.MonkeyPatch, old_key: str) -> None:
    """Writes go to AES-256-GCM under a new key; the old key is read-only."""
    monkeypatch.setattr(config, "TRACECAT__DB_ENCRYPTION_ALGORITHM", AES)
    monkeypatch.setattr(config, "TRACECAT__DB_ENCRYPTION_PREVIOUS_KEYS", (old_key,))


@pytest.fixture
async def rls_bypass(session: AsyncSession) -> AsyncSession:
    await set_rls_context(session, org_id=None, workspace_id=None, bypass=True)
    return session


def _counts(reports: list[ReencryptReport]) -> Counts:
    return {
        r.target: (r.fernet, r.aes256gcm, r.current, r.undecryptable) for r in reports
    }


def _delta(after: Counts, before: Counts) -> Counts:
    """Per-target count changes, so rows other tests committed don't matter."""
    delta: Counts = {}
    for target, counts in after.items():
        diff = tuple(a - b for a, b in zip(counts, before[target], strict=True))
        if any(diff):
            delta[target] = (diff[0], diff[1], diff[2], diff[3])
    return delta


async def _seed(
    session: AsyncSession,
    workspace: Workspace,
    *,
    old_key: str,
    new_key: str,
    seal,
) -> dict[str, uuid.UUID]:
    """Mixed Fernet/AES rows under the old and new keys across four tables."""
    secret_old_fernet = Secret(
        workspace_id=workspace.id,
        name="old-fernet",
        encrypted_keys=seal(b'{"K": "v1"}', key=old_key, algorithm=FERNET),
    )
    secret_old_aes = Secret(
        workspace_id=workspace.id,
        name="old-aes",
        encrypted_keys=seal(b'{"K": "v2"}', key=old_key, algorithm=AES),
    )
    secret_current = Secret(
        workspace_id=workspace.id,
        name="current",
        encrypted_keys=seal(b'{"K": "v3"}', key=new_key, algorithm=AES),
    )
    platform_encrypted = PlatformSetting(
        key=f"test-encrypted-{uuid.uuid4().hex}",
        value=seal(b'"platform"', key=old_key, algorithm=FERNET),
        value_type="string",
        is_encrypted=True,
    )
    platform_plain = PlatformSetting(
        key=f"test-plain-{uuid.uuid4().hex}",
        value=b'"not encrypted"',
        value_type="string",
        is_encrypted=False,
    )
    org_setting = OrganizationSetting(
        organization_id=workspace.organization_id,
        key=f"test-org-{uuid.uuid4().hex}",
        value=seal(b'"org"', key=new_key, algorithm=FERNET),
        value_type="string",
        is_encrypted=True,
    )
    preset = AgentPreset(
        workspace_id=workspace.id,
        name="Reencrypt agent",
        slug=f"reencrypt-agent-{uuid.uuid4().hex[:8]}",
        model_name="test-model",
        model_provider="openai",
    )
    session.add_all(
        [
            secret_old_fernet,
            secret_old_aes,
            secret_current,
            platform_encrypted,
            platform_plain,
            org_setting,
            preset,
        ]
    )
    await session.flush()
    token = AgentChannelToken(
        workspace_id=workspace.id,
        agent_preset_id=preset.id,
        channel_type="slack",
        config={
            "slack_bot_token": ENCRYPTED_CONFIG_VALUE_PREFIX
            + seal(b"xoxb-synthetic", key=old_key, algorithm=FERNET).decode(),
            "slack_client_secret": ENCRYPTED_CONFIG_VALUE_PREFIX
            + seal(b"client-synthetic", key=old_key, algorithm=AES).decode(),
            "slack_signing_secret": "legacy-plaintext",
            "team_id": "T000",
        },
        is_active=True,
    )
    session.add(token)
    await session.commit()
    return {
        "secret_old_fernet": secret_old_fernet.id,
        "secret_old_aes": secret_old_aes.id,
        "secret_current": secret_current.id,
        "platform_encrypted": platform_encrypted.id,
        "platform_plain": platform_plain.id,
        "org_setting": org_setting.id,
        "token": token.id,
    }


async def _snapshot(session: AsyncSession, ids: dict[str, uuid.UUID]) -> list[object]:
    secrets = (
        await session.execute(
            select(Secret.encrypted_keys)
            .where(Secret.id.in_([ids["secret_old_fernet"], ids["secret_old_aes"]]))
            .order_by(Secret.name)
        )
    ).scalars()
    platform = (
        await session.execute(
            select(PlatformSetting.value)
            .where(
                PlatformSetting.id.in_(
                    [ids["platform_encrypted"], ids["platform_plain"]]
                )
            )
            .order_by(PlatformSetting.key)
        )
    ).scalars()
    token_config = await session.scalar(
        select(AgentChannelToken.config).where(AgentChannelToken.id == ids["token"])
    )
    return [*secrets, *platform, token_config]


@pytest.mark.anyio
@pytest.mark.usefixtures("rotate_to_aes")
async def test_dry_run_counts_formats_and_writes_nothing(
    rls_bypass: AsyncSession, svc_workspace: Workspace, old_key, new_key, seal
) -> None:
    session = rls_bypass
    before = _counts(await reencrypt_database(session, key=new_key, dry_run=True))
    ids = await _seed(
        session, svc_workspace, old_key=old_key, new_key=new_key, seal=seal
    )
    snapshot = await _snapshot(session, ids)

    reports = await reencrypt_database(session, key=new_key, dry_run=True)

    # (fernet, aes256gcm, current, undecryptable)
    assert _delta(_counts(reports), before) == {
        "secret.encrypted_keys": (1, 2, 1, 0),
        "platform_settings.value": (1, 0, 0, 0),
        "organization_settings.value": (1, 0, 0, 0),
        "agent_channel_token.config": (1, 1, 0, 0),
    }
    assert all(r.rewritten == 0 and r.conflicts == 0 for r in reports)
    session.expire_all()
    assert await _snapshot(session, ids) == snapshot


@pytest.mark.anyio
@pytest.mark.usefixtures("rotate_to_aes")
async def test_reencrypt_rotates_mixed_rows_and_second_run_is_noop(
    rls_bypass: AsyncSession,
    svc_workspace: Workspace,
    monkeypatch: pytest.MonkeyPatch,
    old_key,
    new_key,
    seal,
) -> None:
    """Old-key Fernet and AES rows move to AES under the new key; plaintext is unchanged."""
    session = rls_bypass
    workspace_id = svc_workspace.id
    ids = await _seed(
        session, svc_workspace, old_key=old_key, new_key=new_key, seal=seal
    )

    reports = await reencrypt_database(session, key=new_key, batch_size=2)

    by_target = {r.target: r for r in reports}
    assert by_target["secret.encrypted_keys"].rewritten >= 2
    assert by_target["agent_channel_token.config"].rewritten >= 1
    assert all(r.undecryptable == 0 and r.conflicts == 0 for r in reports)

    # The old key can now be dropped: everything reads under the new key alone.
    monkeypatch.setattr(config, "TRACECAT__DB_ENCRYPTION_PREVIOUS_KEYS", ())
    session.expire_all()
    secrets = {
        s.name: s.encrypted_keys
        for s in (
            await session.execute(
                select(Secret).where(Secret.workspace_id == workspace_id)
            )
        ).scalars()
    }
    for name, plaintext in [
        ("old-fernet", b'{"K": "v1"}'),
        ("old-aes", b'{"K": "v2"}'),
        ("current", b'{"K": "v3"}'),
    ]:
        decrypted = decrypt_token(secrets[name], key=new_key)
        assert decrypted.plaintext == plaintext
        assert is_current(decrypted, key=new_key)

    platform_encrypted = await session.get(PlatformSetting, ids["platform_encrypted"])
    platform_plain = await session.get(PlatformSetting, ids["platform_plain"])
    org_setting = await session.scalar(
        select(OrganizationSetting).where(OrganizationSetting.id == ids["org_setting"])
    )
    assert platform_encrypted is not None and platform_plain is not None
    assert org_setting is not None
    assert (
        decrypt_token(platform_encrypted.value, key=new_key).plaintext == b'"platform"'
    )
    assert platform_plain.value == b'"not encrypted"'
    org_decrypted = decrypt_token(org_setting.value, key=new_key)
    assert org_decrypted.plaintext == b'"org"'
    assert is_current(org_decrypted, key=new_key)

    token = await session.scalar(
        select(AgentChannelToken).where(AgentChannelToken.id == ids["token"])
    )
    assert token is not None
    for field, plaintext in [
        ("slack_bot_token", b"xoxb-synthetic"),
        ("slack_client_secret", b"client-synthetic"),
    ]:
        stored = token.config[field]
        assert stored.startswith(ENCRYPTED_CONFIG_VALUE_PREFIX)
        decrypted = decrypt_token(
            stored.removeprefix(ENCRYPTED_CONFIG_VALUE_PREFIX).encode(), key=new_key
        )
        assert decrypted.plaintext == plaintext
        assert is_current(decrypted, key=new_key)
    assert token.config["slack_signing_secret"] == "legacy-plaintext"
    assert token.config["team_id"] == "T000"

    snapshot = await _snapshot(session, ids)
    second = await reencrypt_database(session, key=new_key)

    assert all(r.rewritten == 0 and r.undecryptable == 0 for r in second)
    assert all(r.current == r.fernet + r.aes256gcm for r in second)
    session.expire_all()
    assert await _snapshot(session, ids) == snapshot


@pytest.mark.anyio
@pytest.mark.usefixtures("rotate_to_aes")
async def test_undecryptable_value_blocks_writes_unless_skipped(
    rls_bypass: AsyncSession, svc_workspace: Workspace, old_key, new_key, seal
) -> None:
    session = rls_bypass
    ids = await _seed(
        session, svc_workspace, old_key=old_key, new_key=new_key, seal=seal
    )
    unknown_key = Fernet.generate_key().decode()
    lost = Secret(
        workspace_id=svc_workspace.id,
        name="lost",
        encrypted_keys=seal(b'{"K": "lost"}', key=unknown_key, algorithm=AES),
    )
    session.add(lost)
    await session.commit()
    lost_id, lost_value = lost.id, lost.encrypted_keys
    snapshot = await _snapshot(session, ids)

    with pytest.raises(UndecryptableValuesError) as exc_info:
        await reencrypt_database(session, key=new_key)

    by_target = {r.target: r for r in exc_info.value.reports}
    assert by_target["secret.encrypted_keys"].undecryptable == 1
    # Reports carry counts only, never the value.
    assert lost_value.decode() not in repr(exc_info.value.reports)
    session.expire_all()
    assert await _snapshot(session, ids) == snapshot

    reports = await reencrypt_database(session, key=new_key, skip_undecryptable=True)

    by_target = {r.target: r for r in reports}
    assert by_target["secret.encrypted_keys"].undecryptable == 1
    assert by_target["secret.encrypted_keys"].rewritten >= 2
    session.expire_all()
    assert await _snapshot(session, ids) != snapshot
    refreshed = await session.scalar(
        select(Secret.encrypted_keys).where(Secret.id == lost_id)
    )
    assert refreshed == lost_value
    with pytest.raises(InvalidToken):
        decrypt_token(lost_value, key=new_key)


@pytest.mark.anyio
async def test_fernet_target_rewrites_aes_rows_back(
    rls_bypass: AsyncSession,
    svc_workspace: Workspace,
    monkeypatch: pytest.MonkeyPatch,
    old_key,
    seal,
) -> None:
    """Re-encrypting with the Fernet target is the rollback path for AES rows."""
    session = rls_bypass
    monkeypatch.setattr(config, "TRACECAT__DB_ENCRYPTION_ALGORITHM", FERNET)
    secret = Secret(
        workspace_id=svc_workspace.id,
        name="aes-row",
        encrypted_keys=seal(b'{"K": "v"}', key=old_key, algorithm=AES),
    )
    session.add(secret)
    await session.commit()
    secret_id = secret.id

    await reencrypt_database(session, key=old_key)

    session.expire_all()
    stored = await session.scalar(
        select(Secret.encrypted_keys).where(Secret.id == secret_id)
    )
    assert stored is not None
    assert Fernet(old_key).decrypt(stored) == b'{"K": "v"}'
    assert orjson.loads(Fernet(old_key).decrypt(stored)) == {"K": "v"}


@pytest.mark.anyio
@pytest.mark.usefixtures("rotate_to_aes")
async def test_dry_run_leaves_callers_transaction_uncommitted(
    rls_bypass: AsyncSession, new_key
) -> None:
    """A dry run never commits work the caller has pending in the same session."""
    session = rls_bypass
    pending = PlatformSetting(
        key=f"test-pending-{uuid.uuid4().hex}",
        value=b'"pending"',
        value_type="string",
        is_encrypted=False,
    )
    session.add(pending)
    await session.flush()
    pending_key = pending.key

    await reencrypt_database(session, key=new_key, dry_run=True)
    await session.rollback()

    assert (
        await session.scalar(
            select(PlatformSetting.id).where(PlatformSetting.key == pending_key)
        )
        is None
    )
