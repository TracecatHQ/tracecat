"""Short-lived child process that reads secrets from Bitwarden Secrets Manager.

The parent (``bitwarden_secrets_manager.py``) starts one child per fetch with
``python -I <this file>`` and an allow-listed environment, writes one JSON
payload to stdin, and reads one JSON result from stdout. The child logs in
once, reads every requested secret in one call, writes the result and exits.
Nothing is written to disk and nothing outlives the process.

Why a process: the Bitwarden SDK's native calls hold the GIL for the whole
network round trip and have no timeout of their own, so in-process they would
freeze the caller's event loop and could not be cancelled. A child can be
killed.

This module imports only the standard library and the SDK, so it starts fast
and never loads Tracecat or its settings. The parent checks that the SDK is
installed before starting a child. This is the only place that reads
Bitwarden SDK error messages (see :func:`classify_sdk_error`).

Payload (stdin)::

    {"api_url": str, "identity_url": str, "access_token": str,
     "secret_ids": [str, ...], "deadline_seconds": float}

Result (stdout)::

    {"results": {secret_id: {"value": str, "organization_id": str}
                            | {"error": code, "vendor_code": str | null}},
     "fatal": null | {"type": str}}
"""

from __future__ import annotations

import json
import logging
import math
import re
import signal
import sys
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal, Protocol, cast

from bitwarden_sdk import BitwardenClient, ClientSettings, DeviceType


class ErrorCode(StrEnum):
    """The failure codes the child reports; a subset of core's codes."""

    AUTH_FAILED = "auth_failed"
    NOT_FOUND = "not_found"
    ACCESS_DENIED = "access_denied"
    UNAVAILABLE = "unavailable"
    INVALID_REFERENCE = "invalid_reference"
    UNKNOWN = "unknown"


type Phase = Literal["login", "read"]

_MAX_THROTTLE_RETRIES = 2
_SERVER_STATUS = re.compile(r"^Received error message from server: \[(\d{3})\b")
_RETRY_AFTER = re.compile(r"Try again in (\d+)\s*(s|m)\b")
# The SDK's own errors for a malformed access token, raised before any request.
_LOCAL_BAD_TOKEN = ("Doesn't contain a decryption key", "Invalid base64 length")


class SecretsApi(Protocol):
    """The part of the SDK's secrets client the child uses."""

    def get(self, id: str, /) -> Any: ...

    def get_by_ids(self, ids: list[Any], /) -> Any: ...


class AuthApi(Protocol):
    """The part of the SDK's auth client the child uses."""

    def login_access_token(self, access_token: str, /) -> Any: ...


class Client(Protocol):
    """The part of ``BitwardenClient`` the child uses; the test seam."""

    def auth(self) -> AuthApi: ...

    def secrets(self) -> SecretsApi: ...


type ClientFactory = Callable[[str, str], Client]


@dataclass(frozen=True, slots=True)
class Classified:
    """A sanitized failure: a code and a short normalized vendor code."""

    code: ErrorCode
    vendor_code: str | None
    retry_after: float | None = None


def classify_sdk_error(message: str, *, phase: Phase) -> Classified:
    """Classify a Bitwarden SDK error message.

    The SDK raises a bare ``Exception`` whose only content is a message
    string, so the message is the only signal there is. Tracecat otherwise
    never branches on error-message text; this function is the single,
    deliberate exception. The message is never logged and never leaves the
    child: only the code and a short vendor code cross the process boundary.
    The server's errors read ``Received error message from server: [<status>
    <reason>] <body>``, so the HTTP status is the primary key. ``phase`` is
    ``"login"`` or ``"read"``. Each recognized message is pinned to its code
    in the tests; anything else, in either phase, is ``unknown``.
    """
    status_match = _SERVER_STATUS.match(message)
    if status_match:
        status = int(status_match.group(1))
        vendor_code = f"http_{status}"
        if status == 429:
            retry = _RETRY_AFTER.search(message)
            delay = None
            if retry:
                delay = float(retry.group(1)) * (60 if retry.group(2) == "m" else 1)
            return Classified(ErrorCode.UNAVAILABLE, vendor_code, delay)
        if status == 401 or (phase == "login" and status in (400, 403)):
            # A rejected credential is the token's fault; any other status at
            # login (a wrong self-hosted path, a 5xx) is not.
            return Classified(ErrorCode.AUTH_FAILED, vendor_code)
        if phase == "read" and status == 404:
            return Classified(ErrorCode.NOT_FOUND, vendor_code)
        if phase == "read" and status == 403:
            return Classified(ErrorCode.ACCESS_DENIED, vendor_code)
        if 500 <= status <= 599:
            return Classified(ErrorCode.UNAVAILABLE, vendor_code)
        return Classified(ErrorCode.UNKNOWN, vendor_code)
    if message.startswith(("error sending request", "builder error")):
        return Classified(ErrorCode.UNAVAILABLE, "connection")
    if message.startswith("Invalid command value"):
        return Classified(ErrorCode.INVALID_REFERENCE, "invalid_command")
    if phase == "login" and message.startswith(_LOCAL_BAD_TOKEN):
        return Classified(ErrorCode.AUTH_FAILED, "invalid_token")
    return Classified(ErrorCode.UNKNOWN, "unclassified")


def _canonical(secret_id: str) -> str:
    """The canonical lowercase form of a secret ID, or the input if it is not one."""
    try:
        return str(uuid.UUID(secret_id))
    except ValueError:
        return secret_id


def _failure(classified: Classified) -> dict[str, Any]:
    return {"error": classified.code, "vendor_code": classified.vendor_code}


def _value(secret: Any) -> dict[str, Any]:
    return {"value": secret.value, "organization_id": str(secret.organization_id)}


def _call_with_retry[T](
    call: Callable[[], T], *, phase: Phase, deadline: float
) -> T | Classified:
    """Run one SDK call, retrying a throttled one while the deadline allows."""
    for attempt in range(_MAX_THROTTLE_RETRIES + 1):
        try:
            return call()
        except Exception as e:  # noqa: BLE001 - the SDK raises bare Exception
            classified = classify_sdk_error(str(e), phase=phase)
        delay = classified.retry_after
        if (
            delay is None
            or attempt == _MAX_THROTTLE_RETRIES
            or time.monotonic() + delay >= deadline
        ):
            return classified
        time.sleep(delay)
    raise AssertionError("unreachable")  # the loop always returns


def run(payload: Mapping[str, Any], client_factory: ClientFactory) -> dict[str, Any]:
    """Log in once and read every requested secret. Pure apart from the client.

    Returns the result document. Never raises for an SDK failure.
    """
    deadline = time.monotonic() + float(payload["deadline_seconds"])
    secret_ids: Sequence[str] = payload["secret_ids"]
    client = client_factory(payload["api_url"], payload["identity_url"])

    login = _call_with_retry(
        lambda: client.auth().login_access_token(payload["access_token"]),
        phase="login",
        deadline=deadline,
    )
    if isinstance(login, Classified):
        return {"results": dict.fromkeys(secret_ids, _failure(login)), "fatal": None}

    secrets = client.secrets()
    batch = _call_with_retry(
        lambda: secrets.get_by_ids(list(secret_ids)),
        phase="read",
        deadline=deadline,
    )
    if not isinstance(batch, Classified):
        found = {str(secret.id): _value(secret) for secret in batch.data.data}
        # Bitwarden returns canonical lowercase IDs; match requests the same way.
        results = {
            secret_id: found.get(
                _canonical(secret_id),
                {"error": ErrorCode.NOT_FOUND, "vendor_code": "missing"},
            )
            for secret_id in secret_ids
        }
        return {"results": results, "fatal": None}
    if batch.code != ErrorCode.NOT_FOUND or len(secret_ids) == 1:
        return {"results": dict.fromkeys(secret_ids, _failure(batch)), "fatal": None}

    # The batch fails as a whole when one ID is missing or unreadable. Read
    # the IDs one by one on the same login so the error names the right one.
    # Stop at the first failure: the run fails on it anyway, and every extra
    # read counts against Bitwarden's per-minute limit. The parent reports
    # failures in this same order, so a skipped ID is never the one it names.
    results: dict[str, Any] = {}
    failed = False
    for secret_id in secret_ids:
        if failed:
            results[secret_id] = {
                "error": ErrorCode.UNAVAILABLE,
                "vendor_code": "skipped",
            }
            continue
        single = _call_with_retry(
            lambda secret_id=secret_id: secrets.get(secret_id),
            phase="read",
            deadline=deadline,
        )
        if isinstance(single, Classified):
            results[secret_id] = _failure(single)
            failed = True
        else:
            results[secret_id] = _value(single.data)
    return {"results": results, "fatal": None}


def _sdk_client_factory(api_url: str, identity_url: str) -> Client:
    # The SDK's annotations (``state_file: str = None``) do not match a
    # strict protocol; the protocol only names what this module calls.
    client = BitwardenClient(
        ClientSettings(
            api_url=api_url,
            identity_url=identity_url,
            user_agent="Tracecat",
            device_type=DeviceType.SDK,
        )
    )
    return cast(Client, client)


def main() -> int:
    # The SDK forwards its Rust logs into Python logging (pyo3_log). Drop them
    # all; stderr is discarded by the parent too.
    logging.disable(logging.CRITICAL)
    try:
        payload = json.loads(sys.stdin.read())
        # End on our own at the deadline, even if the parent was killed and
        # cannot reap us; SIGALRM's default action terminates the process.
        signal.alarm(math.ceil(float(payload["deadline_seconds"])) + 2)
        result = run(payload, _sdk_client_factory)
    except BaseException as e:  # noqa: BLE001 - a PyO3 panic is not an Exception
        result = {"results": {}, "fatal": {"type": type(e).__name__}}
    sys.stdout.write(json.dumps(result))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
