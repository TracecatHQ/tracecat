"""Core email actions."""
# XXX(WARNING): Do not import __future__ annotations from typing
# This will cause class types to be resolved as strings

import re
import smtplib
import socket
import ssl
from email.message import EmailMessage
from typing import Any

from tracecat_registry import RegistrySecret, registry, secrets

SAFE_EMAIL_PATTERN = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")

smtp_secret = RegistrySecret(
    name="smtp",
    keys=["SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASS"],
)
"""SMTP secret.

- name: `smtp`
- keys:
    - `SMTP_HOST`
    - `SMTP_PORT`
    - `SMTP_USER`
    - `SMTP_PASS`
"""


def _build_email_message(
    sender: str,
    recipients: list[str],
    subject: str,
    body: str,
    bcc: str | list[str] | None = None,
    cc: str | list[str] | None = None,
    reply_to: str | list[str] | None = None,
    headers: dict[str, str] | None = None,
) -> EmailMessage:
    msg = EmailMessage()
    msg.set_content(body)
    msg["From"] = sender
    msg["To"] = recipients
    msg["Subject"] = subject
    if bcc:
        msg["Bcc"] = bcc
    if cc:
        msg["Cc"] = cc
    if reply_to:
        msg["Reply-To"] = reply_to
    if headers:
        for header, value in headers.items():
            msg[header] = value
    return msg


@registry.register(
    namespace="core",
    description="Perform a send email action using SMTP",
    default_title="Send Email (SMTP)",
)
def send_email_smtp(
    sender: str,
    recipients: list[str],
    subject: str,
    body: str,
    timeout: float | None = None,
    headers: dict[str, str] | None = None,
    enable_starttls: bool = False,
    enable_ssl: bool = False,
    enable_auth: bool = False,
    ignore_cert_errors: bool = False,
) -> dict[str, Any]:
    """Run a send email action.

    Parameters
    ----------
    timeout: float | None
        Timeout for the SMTP connection.
        If None, the default socket timeout is used.

    Returns
    -------
    Empty dict if all recipients were accepted.
    Otherwise, a dict with one entry for each recipient that was refused.
    Entry entry contains a tuple of the SMTP error code and the accompanying error message sent by the server.
    """

    timeout = timeout or socket.getdefaulttimeout() or 10.0
    smtp_host = secrets.get("SMTP_HOST")
    smtp_port = int(secrets.get("SMTP_PORT"))
    smtp_user = secrets.get("SMTP_USER")
    smtp_pass = secrets.get("SMTP_PASS")

    msg = _build_email_message(sender, recipients, subject, body, headers=headers)

    context = ssl.create_default_context()
    if ignore_cert_errors:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE

    create_smtp_server = smtplib.SMTP_SSL if enable_ssl else smtplib.SMTP

    with create_smtp_server(host=smtp_host, port=smtp_port, timeout=timeout) as server:
        if enable_starttls:
            server.starttls(context=context)
        if enable_auth:
            server.login(smtp_user, smtp_pass)
        response = server.send_message(msg)

    return response
