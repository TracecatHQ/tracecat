"""Add agent channel bots and thread links, with bots copied from the legacy tokens.

Revision ID: e20249352688
Revises: ec392d96f2b0
Create Date: 2026-11-10 12:00:00.000000

Copies each Slack ``agent_channel_token`` into ``agent_channel_bot`` with
``bot.id = token.id``, so a copied bot keeps the legacy endpoint URL. Legacy
threads link at runtime on their next mention. The legacy table stays until a
later release.

The token copy decrypts legacy secrets and reseals them as one credentials
document, so it needs an online upgrade, and ``TRACECAT__DB_ENCRYPTION_KEY``
whenever a Slack token exists. Tokens whose secrets do not decrypt are copied
without credentials, as ``needs_reconnect``.
"""

import logging
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime

import orjson
import sqlalchemy as sa
from cryptography.fernet import InvalidToken
from sqlalchemy.dialects import postgresql

from alembic import context, op
from tracecat.auth.secrets import get_db_encryption_key
from tracecat.db.tenant_rls import (
    disable_workspace_table_rls,
    enable_workspace_table_rls,
)

# revision identifiers, used by Alembic.
revision: str = "e20249352688"
down_revision: str | None = "ec392d96f2b0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.runtime.migration.agent_channel_bots")

TABLES = ("agent_channel_bot", "agent_channel_thread")

# Legacy token config markers, frozen here: the legacy channel modules go away.
LEGACY_PENDING_BOT_TOKEN = "__tracecat_pending_bot_token__"
LEGACY_PENDING_SIGNING_SECRET_PREFIX = "__tracecat_pending_signing_secret__"
LEGACY_ENCRYPTED_PREFIX = "__tracecat_encrypted__:"

# The columns the copy writes, frozen: later bot columns must not change it.
_bots = sa.table(
    "agent_channel_bot",
    sa.column("id", sa.UUID()),
    sa.column("workspace_id", sa.UUID()),
    sa.column("agent_preset_id", sa.UUID()),
    sa.column("provider", sa.String()),
    sa.column("status", sa.String()),
    sa.column("granted_scopes", postgresql.ARRAY(sa.String())),
    sa.column("manifest_version", sa.Integer()),
    sa.column("encrypted_credentials", sa.LargeBinary()),
    sa.column("created_at", sa.TIMESTAMP(timezone=True)),
    sa.column("updated_at", sa.TIMESTAMP(timezone=True)),
)


@dataclass(frozen=True, slots=True)
class _SlackCredentials:
    """The bot's credentials document, frozen at the shape this copy writes."""

    bot_token: str | None = None
    signing_secret: str | None = None
    client_id: str | None = None
    client_secret: str | None = None

    def seal(self) -> bytes:
        from tracecat.secrets.encryption import encrypt_value  # See _legacy_value.

        document = {k: v for k, v in asdict(self).items() if v is not None}
        return encrypt_value(orjson.dumps(document), key=get_db_encryption_key())


def _legacy_value(raw: object) -> str | None:
    # Lazy: it imports tracecat.db.models, which scripts/check_migrations.py can't load.
    from tracecat.secrets.encryption import decrypt_value

    if not isinstance(raw, str) or not raw:
        return None
    if not raw.startswith(LEGACY_ENCRYPTED_PREFIX):
        return raw  # Plaintext rows predate config encryption.
    encrypted = raw.removeprefix(LEGACY_ENCRYPTED_PREFIX).encode()
    # A missing key raises ValueError: a misconfigured deploy fails, not each row.
    return decrypt_value(encrypted, key=get_db_encryption_key()).decode()


def _legacy_credentials(config: Mapping[str, object]) -> _SlackCredentials:
    """The token's Slack secrets without the legacy placeholders; empty if unreadable."""
    try:
        bot_token = _legacy_value(config.get("slack_bot_token"))
        signing_secret = _legacy_value(config.get("slack_signing_secret"))
        client_secret = _legacy_value(config.get("slack_client_secret"))
    except InvalidToken:
        return _SlackCredentials()
    if bot_token == LEGACY_PENDING_BOT_TOKEN:
        bot_token = None
    if signing_secret and signing_secret.startswith(
        LEGACY_PENDING_SIGNING_SECRET_PREFIX
    ):
        signing_secret = None
    client_id = config.get("slack_client_id")
    return _SlackCredentials(
        bot_token=bot_token,
        signing_secret=signing_secret,
        client_id=client_id if isinstance(client_id, str) and client_id else None,
        client_secret=client_secret,
    )


@dataclass(frozen=True, slots=True)
class _LegacyToken:
    id: uuid.UUID
    workspace_id: uuid.UUID
    agent_preset_id: uuid.UUID
    config: Mapping[str, object]
    is_active: bool
    created_at: datetime
    updated_at: datetime


def _bot_values(token: _LegacyToken) -> dict[str, object]:
    """The bot row for a legacy token. Install metadata stays NULL: tokens kept only secrets."""
    credentials = _legacy_credentials(token.config)
    if credentials.bot_token is None or credentials.signing_secret is None:
        status = "needs_reconnect"
        logger.warning("Legacy channel token %s needs a reconnect", token.id)
    else:
        status = "active" if token.is_active else "paused"
    return {
        "id": token.id,
        "workspace_id": token.workspace_id,
        "agent_preset_id": token.agent_preset_id,
        "provider": "slack",
        "status": status,
        "granted_scopes": [],
        "manifest_version": 0,
        "encrypted_credentials": credentials.seal(),
        "created_at": token.created_at,
        "updated_at": token.updated_at,
    }


def _copy_legacy_tokens(connection: sa.Connection) -> None:
    tokens = connection.execute(
        sa.text(
            "SELECT id, workspace_id, agent_preset_id, config, is_active, created_at,"
            " updated_at FROM agent_channel_token WHERE channel_type = 'slack'"
        )
    ).mappings()
    rows = [_bot_values(_LegacyToken(**token)) for token in tokens]
    if rows:
        connection.execute(
            postgresql.insert(_bots)
            .values(rows)
            .on_conflict_do_nothing(index_elements=["id"])
        )


def upgrade() -> None:
    if context.is_offline_mode():
        # Decrypting needs the rows; offline SQL would record a copy that never ran.
        raise RuntimeError("Copying legacy channel tokens needs an online upgrade")
    # The foreign keys lock agent_session and agent_preset against writes; fail
    # fast rather than queue behind a long transaction.
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.create_table(
        "agent_channel_bot",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("agent_preset_id", sa.UUID(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("status", sa.String(), server_default="active", nullable=False),
        sa.Column("provider_tenant_id", sa.String(), nullable=True),
        sa.Column("provider_enterprise_id", sa.String(), nullable=True),
        sa.Column("provider_app_id", sa.String(), nullable=True),
        sa.Column("provider_bot_user_id", sa.String(), nullable=True),
        sa.Column(
            "granted_scopes",
            postgresql.ARRAY(sa.String()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("manifest_version", sa.Integer(), nullable=False),
        sa.Column("encrypted_credentials", sa.LargeBinary(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "provider IN ('slack')", name=op.f("ck_agent_channel_bot_provider")
        ),
        sa.CheckConstraint(
            "status IN ('active', 'paused', 'disconnected', 'needs_reconnect')",
            name=op.f("ck_agent_channel_bot_status"),
        ),
        sa.ForeignKeyConstraint(
            ["agent_preset_id"],
            ["agent_preset.id"],
            name=op.f("fk_agent_channel_bot_agent_preset_id_agent_preset"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.id"],
            name=op.f("fk_agent_channel_bot_workspace_id_workspace"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_channel_bot")),
        sa.UniqueConstraint(
            "id", "workspace_id", name=op.f("uq_agent_channel_bot_id_workspace_id")
        ),
        sa.UniqueConstraint(
            "provider",
            "provider_tenant_id",
            "provider_app_id",
            name=op.f(
                "uq_agent_channel_bot_provider_provider_tenant_id_provider_app_id"
            ),
        ),
    )
    op.create_index(
        op.f("ix_agent_channel_bot_agent_preset_id"),
        "agent_channel_bot",
        ["agent_preset_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_agent_channel_bot_workspace_id"),
        "agent_channel_bot",
        ["workspace_id"],
        unique=False,
    )
    op.create_table(
        "agent_channel_thread",
        sa.Column("session_id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("bot_id", sa.UUID(), nullable=False),
        sa.Column("thread", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("thread_key", sa.String(), nullable=False),
        sa.Column("sync_enabled", sa.Boolean(), server_default="true", nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["bot_id", "workspace_id"],
            ["agent_channel_bot.id", "agent_channel_bot.workspace_id"],
            name=op.f("fk_agent_channel_thread_bot_id_workspace_id_agent_channel_bot"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["session_id", "workspace_id"],
            ["agent_session.id", "agent_session.workspace_id"],
            name=op.f("fk_agent_channel_thread_session_id_workspace_id_agent_session"),
            ondelete="CASCADE",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.id"],
            name=op.f("fk_agent_channel_thread_workspace_id_workspace"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("session_id", name=op.f("pk_agent_channel_thread")),
        sa.UniqueConstraint(
            "bot_id",
            "thread_key",
            name=op.f("uq_agent_channel_thread_bot_id_thread_key"),
        ),
    )
    for table in TABLES:
        op.execute(enable_workspace_table_rls(table))

    _copy_legacy_tokens(op.get_bind())


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '10s'")
    for table in reversed(TABLES):
        op.execute(disable_workspace_table_rls(table))
    op.drop_table("agent_channel_thread")
    op.drop_index(
        op.f("ix_agent_channel_bot_workspace_id"), table_name="agent_channel_bot"
    )
    op.drop_index(
        op.f("ix_agent_channel_bot_agent_preset_id"), table_name="agent_channel_bot"
    )
    op.drop_table("agent_channel_bot")
