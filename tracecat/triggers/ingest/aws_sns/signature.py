"""SNS message checks: signing certificate, signature, freshness.

The signing certificate fetch is the only outbound call whose target comes
from message content, so the URL is checked against one exact host (the SNS
host of the pinned topic's region) and one path shape, then rebuilt from the
checked parts before it is fetched with no redirects, a short timeout and a
size cap.

Reference:
- Verifying message signatures:
  https://docs.aws.amazon.com/sns/latest/dg/sns-verify-signature-of-message.html
"""

from __future__ import annotations

import asyncio
import base64
import re
from collections import OrderedDict
from datetime import datetime, timedelta
from urllib.parse import urlsplit

import httpx
from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from tracecat.triggers.ingest.aws_sns.schemas import SnsEnvelope
from tracecat.triggers.ingest.aws_sns.types import SnsTopic
from tracecat.triggers.ingest.types import IngestError, IngestErrorCode

# AWS names signing certificates SimpleNotificationService-<id>.pem.
_CERT_PATH_PATTERN = re.compile(r"/SimpleNotificationService-[A-Za-z0-9]{1,64}\.pem")
# A PEM certificate is a few KB.
MAX_CERT_BYTES = 16 * 1024
CERT_FETCH_TIMEOUT_SECONDS = 5.0
# Certificates rotate under new URLs, so a small cache keyed by URL holds the
# few in use. Expired entries are dropped when read.
_CERT_CACHE_SIZE = 32
_cert_cache: OrderedDict[str, x509.Certificate] = OrderedDict()

# The dedupe horizon: a redelivered event is caught by the outbox while it is
# queued, then by the dispatcher's fixed run IDs for as long as Temporal keeps
# the run (24 hours self-hosted). An older signed message is refused, so a
# captured one cannot start a run again later. This is far past SNS's one-hour
# retry policy and any realistic throttled backlog.
MAX_MESSAGE_AGE = timedelta(hours=24)
MAX_CLOCK_SKEW = timedelta(minutes=15)


def _invalid_signature(message: str) -> IngestError:
    return IngestError(IngestErrorCode.SIGNATURE_INVALID, message)


def _unavailable(message: str) -> IngestError:
    return IngestError(IngestErrorCode.UNAVAILABLE, message)


def signing_cert_url(raw_url: str, *, topic: SnsTopic) -> str:
    """Check ``SigningCertURL`` and rebuild it from the checked parts.

    The host must be exactly the SNS host of the topic's region: no port, no
    user info, no query or fragment, and a ``SimpleNotificationService-*.pem``
    path.

    Raises:
        IngestError: ``SIGNATURE_INVALID`` if the URL is not an SNS
            certificate URL for the topic's region.
    """
    try:
        parts = urlsplit(raw_url)
    except ValueError:
        raise _invalid_signature("Signing certificate URL is not valid") from None
    if (
        parts.scheme != "https"
        # netloc equal to the bare host rules out user info and ports.
        or parts.netloc != topic.sns_host
        or parts.query
        or parts.fragment
        or not _CERT_PATH_PATTERN.fullmatch(parts.path)
    ):
        raise _invalid_signature("Signing certificate URL is not an SNS URL")
    return f"https://{topic.sns_host}{parts.path}"


def _cert_http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(follow_redirects=False, timeout=CERT_FETCH_TIMEOUT_SECONDS)


async def _download_cert(url: str) -> bytes:
    try:
        async with asyncio.timeout(CERT_FETCH_TIMEOUT_SECONDS * 2):
            async with _cert_http_client() as client:
                async with client.stream("GET", url) as response:
                    if response.status_code >= 500:
                        raise _unavailable("Signing certificate host is unavailable")
                    if response.status_code != 200:
                        # Redirects included: they are never followed.
                        raise _invalid_signature("Signing certificate not served")
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > MAX_CERT_BYTES:
                            raise _invalid_signature("Signing certificate too large")
                    return bytes(body)
    except (TimeoutError, httpx.RequestError):
        # RequestError covers transport failures and body decoding errors.
        raise _unavailable("Signing certificate could not be fetched") from None


def _check_validity(cert: x509.Certificate, now: datetime) -> bool:
    return cert.not_valid_before_utc <= now <= cert.not_valid_after_utc


async def get_signing_cert(url: str, *, now: datetime) -> x509.Certificate:
    """Return the certificate at a checked SNS URL, from the cache if held.

    Raises:
        IngestError: ``SIGNATURE_INVALID`` if it cannot be trusted,
            ``UNAVAILABLE`` if the host could not be reached.
    """
    if (cached := _cert_cache.get(url)) is not None:
        if _check_validity(cached, now):
            return cached
        del _cert_cache[url]

    pem = await _download_cert(url)
    try:
        cert = x509.load_pem_x509_certificate(pem)
    except ValueError:
        raise _invalid_signature("Signing certificate is not a certificate") from None
    if not _check_validity(cert, now):
        raise _invalid_signature("Signing certificate is not valid now")

    _cert_cache[url] = cert
    while len(_cert_cache) > _CERT_CACHE_SIZE:
        _cert_cache.popitem(last=False)
    return cert


def _parse_timestamp(raw: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        raise IngestError(
            IngestErrorCode.MALFORMED, "Timestamp is not ISO 8601"
        ) from None
    if parsed.tzinfo is None:
        raise IngestError(IngestErrorCode.MALFORMED, "Timestamp has no time zone")
    return parsed


async def verify_envelope(
    envelope: SnsEnvelope, *, topic: SnsTopic, now: datetime
) -> None:
    """Verify the SNS signature (versions 1 and 2) and the message's age.

    Call only after ``envelope.topic_arn`` matched the pinned topic: the
    certificate host is derived from that topic.

    Raises:
        IngestError: ``SIGNATURE_INVALID``, ``STALE_MESSAGE``, ``MALFORMED``
            or ``UNAVAILABLE``.
    """
    url = signing_cert_url(envelope.signing_cert_url, topic=topic)
    try:
        signature = base64.b64decode(envelope.signature, validate=True)
    except ValueError:
        # binascii.Error for bad base64; plain ValueError for non-ASCII text.
        raise _invalid_signature("Signature is not base64") from None

    cert = await get_signing_cert(url, now=now)
    public_key = cert.public_key()
    if not isinstance(public_key, rsa.RSAPublicKey):
        raise _invalid_signature("Signing certificate key is not RSA")
    algorithm = hashes.SHA1() if envelope.signature_version == "1" else hashes.SHA256()
    try:
        public_key.verify(
            signature,
            envelope.string_to_sign().encode(),
            padding.PKCS1v15(),
            algorithm,
        )
    except InvalidSignature:
        raise _invalid_signature("Signature does not match") from None

    # Checked after the signature, so the timestamp read is the signed one.
    sent_at = _parse_timestamp(envelope.timestamp)
    if sent_at < now - MAX_MESSAGE_AGE or sent_at > now + MAX_CLOCK_SKEW:
        raise IngestError(IngestErrorCode.STALE_MESSAGE, "Message is too old")
