"""Core email actions."""
# XXX(WARNING): Do not import __future__ annotations from typing
# This will cause class types to be resolved as strings

import json
import re
import smtplib
import socket
import ssl
from copy import deepcopy
from email.message import EmailMessage
from typing import Annotated, Any, Literal

import nh3
from tracecat.config import TRACECAT__ALLOWED_EMAIL_ATTRIBUTES
from typing_extensions import Doc

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
    content_type: str,
    bcc: str | list[str] | None = None,
    cc: str | list[str] | None = None,
    reply_to: str | list[str] | None = None,
    headers: dict[str, str] | None = None,
    sender_prefix: str | None = None,
) -> EmailMessage:
    msg = EmailMessage()

    # Handle content based on content_type
    if content_type == "text/html":
        # Follow MIME standards for HTML emails
        # https://www.w3.org/Protocols/rfc1341/7_2_Multipart.html
        msg.add_alternative(
            "This email requires an HTML viewer to display properly.", subtype="plain"
        )
        if TRACECAT__ALLOWED_EMAIL_ATTRIBUTES:
            ALLOWED_ATTRIBUTES = deepcopy(nh3.ALLOWED_ATTRIBUTES)
            for tag, attributes in json.loads(
                TRACECAT__ALLOWED_EMAIL_ATTRIBUTES
            ).items():
                ALLOWED_ATTRIBUTES[tag] = set(attributes)
            sanitized_body = nh3.clean(body, attributes=ALLOWED_ATTRIBUTES)
        else:
            sanitized_body = nh3.clean(body)
        msg.add_alternative(sanitized_body, subtype="html")
    elif content_type == "text/plain":
        msg.set_content(body)
    else:
        raise ValueError(
            f"Unsupported content type: {content_type}. Expected 'text/plain' or 'text/html'."
        )

    formatted_sender = f"{sender_prefix} <{sender}>" if sender_prefix else sender

    msg["From"] = formatted_sender
    msg["To"] = recipients
    msg["Subject"] = subject

    # Check email pattern
    if not SAFE_EMAIL_PATTERN.match(sender):
        raise ValueError(f"Invalid sender email address: {sender}")
    if not all(SAFE_EMAIL_PATTERN.match(recipient) for recipient in recipients):
        raise ValueError(f"Invalid recipient email address: {recipients}")

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
    secrets=[smtp_secret],
)
def send_email_smtp(
    sender: Annotated[
        str,
        Doc("Email address of the sender"),
    ],
    recipients: Annotated[
        list[str],
        Doc("List of recipient email addresses"),
    ],
    subject: Annotated[
        str,
        Doc("Subject of the email"),
    ],
    body: Annotated[
        str,
        Doc("Body content of the email"),
    ],
    content_type: Annotated[
        Literal["text/plain", "text/html"],
        Doc(
            "Email content type ('text/plain' or 'text/html'). Defaults to 'text/plain'."
        ),
    ] = "text/plain",
    # Additional recipients
    bcc: Annotated[
        str | list[str] | None,
        Doc("Recipient(s) to be copied in the BCC field"),
    ] = None,
    cc: Annotated[
        str | list[str] | None,
        Doc("Recipient(s) to be copied in the CC field"),
    ] = None,
    reply_to: Annotated[
        str | list[str] | None,
        Doc("Email address(es) to be used in the Reply-To field"),
    ] = None,
    # Email customization
    headers: Annotated[
        dict[str, str] | None,
        Doc("Additional email headers"),
    ] = None,
    sender_prefix: Annotated[
        str | None,
        Doc("Email address prefix of the sender"),
    ] = None,
    # SMTP connection settings
    timeout: Annotated[
        float | None,
        Doc("Timeout for SMTP operations in seconds"),
    ] = None,
    enable_starttls: Annotated[
        bool,
        Doc("Enable STARTTLS for secure connection"),
    ] = False,
    enable_ssl: Annotated[
        bool,
        Doc("Enable SSL for secure connection"),
    ] = False,
    enable_auth: Annotated[
        bool,
        Doc("Enable SMTP authentication"),
    ] = False,
    ignore_cert_errors: Annotated[
        bool,
        Doc("Ignore SSL certificate errors"),
    ] = False,
) -> dict[str, Any]:
    """Run a send email action.

    Returns
    -------
    Empty dict if all recipients were accepted.
    Otherwise, a dict with one entry for each recipient that was refused.
    Entry entry contains a tuple of the SMTP error code and the accompanying error message sent by the server.
    """

    timeout = timeout or socket.getdefaulttimeout() or 10.0
    smtp_host = secrets.get("SMTP_HOST")
    try:
        smtp_port = int(secrets.get("SMTP_PORT"))
    except TypeError as e:
        raise ValueError(
            f"Expected SMTP port to be an integer. Got: {secrets.get('SMTP_PORT')}"
        ) from e
    smtp_user = secrets.get("SMTP_USER")
    smtp_pass = secrets.get("SMTP_PASS")

    msg = _build_email_message(
        bcc=bcc,
        body=body,
        cc=cc,
        content_type=content_type,
        headers=headers,
        recipients=recipients,
        reply_to=reply_to,
        sender_prefix=sender_prefix,
        sender=sender,
        subject=subject,
    )

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
