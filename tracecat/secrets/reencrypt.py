"""Re-encrypt stored database values with the current algorithm and key.

Run it with the same environment as the API service::

    python -m tracecat.secrets.reencrypt --dry-run
    python -m tracecat.secrets.reencrypt

Every run first scans all encrypted values. A dry run stops there and prints
counts. Otherwise the run refuses to write when any value fails to decrypt
under ``TRACECAT__DB_ENCRYPTION_KEY`` and ``TRACECAT__DB_ENCRYPTION_PREVIOUS_KEYS``,
unless ``--skip-undecryptable`` is passed. Writes then go in small committed
batches. Each update is a compare-and-swap on the old ciphertext, so a value the
application rewrote mid-run is left alone. Values already in the target format
and key are skipped, so the command is safe to interrupt and run again.
"""

import argparse
import asyncio
import sys
from collections.abc import AsyncIterator, Sequence
from typing import Any, Final

from cryptography.fernet import InvalidToken
from sqlalchemy import ColumnElement, or_, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from tracecat import config
from tracecat.agent.channels.service import (
    ENCRYPTED_CONFIG_VALUE_PREFIX,
    SLACK_ENCRYPTED_CONFIG_FIELDS,
)
from tracecat.auth.secrets import get_db_encryption_key
from tracecat.config import DBEncryptionAlgorithm
from tracecat.db.engine import get_async_session_bypass_rls_context_manager
from tracecat.db.models import (
    AgentCatalog,
    AgentChannelToken,
    AgentCustomProvider,
    MCPIntegration,
    MCPRefreshToken,
    OAuthIntegration,
    OrganizationSecret,
    OrganizationSetting,
    PlatformSecret,
    PlatformSetting,
    Secret,
    WorkspaceTunnel,
)
from tracecat.secrets.encryption import (
    decrypt_token,
    encrypt_token,
    is_current,
    key_id,
)
from tracecat.secrets.types import (
    EncryptedColumn,
    EncryptedJSONFields,
    ReencryptPass,
    ReencryptReport,
)

DEFAULT_BATCH_SIZE: Final[int] = 200


ENCRYPTED_COLUMNS: Final[tuple[EncryptedColumn, ...]] = (
    EncryptedColumn(Secret.encrypted_keys),
    EncryptedColumn(OrganizationSecret.encrypted_keys),
    EncryptedColumn(PlatformSecret.encrypted_keys),
    EncryptedColumn(MCPIntegration.encrypted_headers),
    EncryptedColumn(MCPIntegration.encrypted_stdio_env),
    EncryptedColumn(MCPRefreshToken.encrypted_metadata),
    EncryptedColumn(OAuthIntegration.encrypted_access_token),
    EncryptedColumn(OAuthIntegration.encrypted_refresh_token),
    EncryptedColumn(OAuthIntegration.encrypted_client_id),
    EncryptedColumn(OAuthIntegration.encrypted_client_secret),
    EncryptedColumn(AgentCustomProvider.encrypted_config),
    EncryptedColumn(AgentCatalog.encrypted_config),
    EncryptedColumn(WorkspaceTunnel.encrypted_client_secret),
    EncryptedColumn(WorkspaceTunnel.encrypted_gateway_key),
    EncryptedColumn(
        OrganizationSetting.value, only_if=OrganizationSetting.is_encrypted.is_(True)
    ),
    EncryptedColumn(
        PlatformSetting.value, only_if=PlatformSetting.is_encrypted.is_(True)
    ),
)
"""Every binary column holding DB-encrypted values. Tests check it against the models."""

ENCRYPTED_JSON_FIELDS: Final[tuple[EncryptedJSONFields, ...]] = (
    EncryptedJSONFields(
        AgentChannelToken.config,
        fields=SLACK_ENCRYPTED_CONFIG_FIELDS,
        prefix=ENCRYPTED_CONFIG_VALUE_PREFIX,
    ),
)
"""Every JSONB column holding DB-encrypted values inside its fields."""


class UndecryptableValuesError(Exception):
    """Raised when a value fails to decrypt and skipping was not requested."""

    def __init__(self, reports: list[ReencryptReport]) -> None:
        super().__init__("Some encrypted values could not be decrypted")
        self.reports = reports


def _reseal(
    value: bytes, *, run: ReencryptPass, report: ReencryptReport
) -> bytes | None:
    """Return the value re-encrypted for the target, or None when no write is needed."""
    try:
        decrypted = decrypt_token(value, key=run.key)
    except InvalidToken:
        report.undecryptable += 1
        if run.write and not run.skip_undecryptable:
            raise UndecryptableValuesError([report]) from None
        return None

    match decrypted.algorithm:
        case DBEncryptionAlgorithm.FERNET:
            report.fernet += 1
        case DBEncryptionAlgorithm.AES256GCM:
            report.aes256gcm += 1
    if is_current(decrypted, key=run.key):
        report.current += 1
        return None
    return encrypt_token(decrypted.plaintext, key=run.key)


async def _batches(
    session: AsyncSession,
    attr: InstrumentedAttribute[Any],
    where: list[ColumnElement[bool]],
    batch_size: int,
) -> AsyncIterator[Sequence[tuple[Any, Any]]]:
    """Yield ``(primary key, value)`` rows in primary-key order."""
    pk = attr.parent.mapper.primary_key[0]
    last_pk: Any = None
    while True:
        stmt = select(pk, attr).where(*where).order_by(pk).limit(batch_size)
        if last_pk is not None:
            stmt = stmt.where(pk > last_pk)
        rows = (await session.execute(stmt)).tuples().all()
        if not rows:
            return
        yield rows
        last_pk = rows[-1][0]


async def _compare_and_swap(
    session: AsyncSession,
    attr: InstrumentedAttribute[Any],
    row_pk: Any,
    old: Any,
    new: Any,
    report: ReencryptReport,
) -> None:
    pk = attr.parent.mapper.primary_key[0]
    result = await session.execute(
        update(attr.parent.mapper.class_)
        .where(pk == row_pk, attr == old)
        .values({attr: new})
        .execution_options(synchronize_session=False)
    )
    if result.rowcount == 1:  # pyright: ignore[reportAttributeAccessIssue]
        report.rewritten += 1
    else:
        report.conflicts += 1


async def _process_column(
    session: AsyncSession, column: EncryptedColumn, run: ReencryptPass
) -> ReencryptReport:
    report = ReencryptReport(target=column.label)
    where = [column.attr.is_not(None), column.attr != b""]
    if column.only_if is not None:
        where.append(column.only_if)
    async for rows in _batches(session, column.attr, where, run.batch_size):
        for row_pk, value in rows:
            new_value = _reseal(value, run=run, report=report)
            if new_value is not None and run.write:
                await _compare_and_swap(
                    session, column.attr, row_pk, value, new_value, report
                )
        if run.write:
            await session.commit()
    return report


async def _process_json_fields(
    session: AsyncSession, target: EncryptedJSONFields, run: ReencryptPass
) -> ReencryptReport:
    report = ReencryptReport(target=target.label)
    where = [
        or_(
            *(
                target.attr[f].astext.startswith(target.prefix, autoescape=True)
                for f in target.fields
            )
        )
    ]
    async for rows in _batches(session, target.attr, where, run.batch_size):
        for row_pk, stored in rows:
            updated = dict(stored)
            for field in target.fields:
                raw = stored.get(field)
                if not isinstance(raw, str) or not raw.startswith(target.prefix):
                    continue
                token = raw.removeprefix(target.prefix).encode("utf-8")
                new_token = _reseal(token, run=run, report=report)
                if new_token is not None:
                    updated[field] = target.prefix + new_token.decode("ascii")
            if updated != stored and run.write:
                await _compare_and_swap(
                    session, target.attr, row_pk, stored, updated, report
                )
        if run.write:
            await session.commit()
    return report


async def _process_all(
    session: AsyncSession, run: ReencryptPass
) -> list[ReencryptReport]:
    reports = [await _process_column(session, c, run) for c in ENCRYPTED_COLUMNS]
    for target in ENCRYPTED_JSON_FIELDS:
        reports.append(await _process_json_fields(session, target, run))
    return reports


async def reencrypt_database(
    session: AsyncSession,
    *,
    key: str,
    dry_run: bool = False,
    skip_undecryptable: bool = False,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> list[ReencryptReport]:
    """Scan every encrypted value, then rewrite stale ones for the current target.

    Args:
        session: A session that can read and write every tenant's rows.
        key: The current ``TRACECAT__DB_ENCRYPTION_KEY``.
        dry_run: Return scan counts without writing or committing.
        skip_undecryptable: Rewrite the rest when some values fail to decrypt.
        batch_size: Rows per read and per committed write batch.

    Returns:
        Per-column counts from the scan (dry run) or the write pass.

    Raises:
        UndecryptableValuesError: If a value fails to decrypt and
            ``skip_undecryptable`` is false. No batch containing it is committed.
    """
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")
    scan = await _process_all(
        session,
        ReencryptPass(
            key=key, write=False, skip_undecryptable=True, batch_size=batch_size
        ),
    )
    if dry_run:
        return scan
    if not skip_undecryptable and any(r.undecryptable for r in scan):
        raise UndecryptableValuesError(scan)
    return await _process_all(
        session,
        ReencryptPass(
            key=key,
            write=True,
            skip_undecryptable=skip_undecryptable,
            batch_size=batch_size,
        ),
    )


def _print_reports(reports: list[ReencryptReport], *, dry_run: bool) -> None:
    header = ["target", "fernet", "aes256gcm", "current", "undecryptable"]
    if not dry_run:
        header += ["rewritten", "conflicts"]
    rows = [header]
    for r in reports:
        counts = [r.fernet, r.aes256gcm, r.current, r.undecryptable]
        if not dry_run:
            counts += [r.rewritten, r.conflicts]
        rows.append([r.target, *(str(c) for c in counts)])
    widths = [max(len(row[i]) for row in rows) for i in range(len(header))]
    for row in rows:
        print(
            "  ".join(
                cell.ljust(width) for cell, width in zip(row, widths, strict=True)
            )
        )


async def _run_cli(args: argparse.Namespace) -> int:
    key = get_db_encryption_key()
    print(
        f"Target: {config.TRACECAT__DB_ENCRYPTION_ALGORITHM} "
        f"under key id {key_id(key)}"
        f"{' (dry run)' if args.dry_run else ''}"
    )
    try:
        async with get_async_session_bypass_rls_context_manager() as session:
            reports = await reencrypt_database(
                session,
                key=key,
                dry_run=args.dry_run,
                skip_undecryptable=args.skip_undecryptable,
                batch_size=args.batch_size,
            )
    except UndecryptableValuesError as e:
        _print_reports(e.reports, dry_run=True)
        print(
            "Aborted: some values could not be decrypted with the configured "
            "keys. Add the missing key to TRACECAT__DB_ENCRYPTION_PREVIOUS_KEYS, "
            "or pass --skip-undecryptable to leave those values as they are.",
            file=sys.stderr,
        )
        return 1
    except SQLAlchemyError as e:
        # Database errors carry the statement's bound parameters, which here are
        # ciphertext. Report the error type only, never its message.
        print(
            f"Aborted: database error ({type(e).__name__}). Committed batches are "
            "kept; run the command again to resume.",
            file=sys.stderr,
        )
        return 1
    _print_reports(reports, dry_run=args.dry_run)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tracecat.secrets.reencrypt",
        description=(
            "Re-encrypt stored database values with "
            "TRACECAT__DB_ENCRYPTION_ALGORITHM and TRACECAT__DB_ENCRYPTION_KEY."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Count values per column and format without writing.",
    )
    parser.add_argument(
        "--skip-undecryptable",
        action="store_true",
        help="Leave values that no configured key can decrypt unchanged.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f"Rows per committed batch (default {DEFAULT_BATCH_SIZE}).",
    )
    args = parser.parse_args(argv)
    if args.batch_size < 1:
        parser.error("--batch-size must be at least 1")
    return asyncio.run(_run_cli(args))


if __name__ == "__main__":
    raise SystemExit(main())
