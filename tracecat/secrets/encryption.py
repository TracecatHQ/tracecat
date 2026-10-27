"""Database value encryption.

Two ciphertext formats are readable:

- Fernet (AES-128-CBC + HMAC-SHA256): a urlsafe base64 token. Every value
  without the AES-256-GCM prefix is treated as Fernet.
- AES-256-GCM: ``tc1:<key id>:<urlsafe base64 of nonce || ciphertext || tag>``.
  The ``tc1:<key id>:`` header is authenticated as associated data. ``:`` is
  outside the urlsafe base64 alphabet, so no Fernet token can start with the
  prefix.

``TRACECAT__DB_ENCRYPTION_ALGORITHM`` picks the format for new writes. Reads
accept both formats under the given key or any key in
``TRACECAT__DB_ENCRYPTION_PREVIOUS_KEYS``.

The AES-256-GCM key is derived from the 32 bytes of the Fernet-format
``TRACECAT__DB_ENCRYPTION_KEY`` with HKDF-SHA256. The key id is a separate HKDF
output, so it identifies a key without revealing anything about it.
"""

import base64
import binascii
import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Final

import orjson
from cryptography.exceptions import InvalidTag
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from tracecat import config
from tracecat.config import DBEncryptionAlgorithm
from tracecat.secrets.enums import SecretType

from .schemas import SecretBase, SecretKeyValue

AES256GCM_PREFIX: Final[bytes] = b"tc1:"
_AES256GCM_KEY_INFO: Final[bytes] = b"tracecat-db-encryption:aes256gcm:v1"
_KEY_ID_INFO: Final[bytes] = b"tracecat-db-encryption:key-id:v1"
_KEY_ID_BYTES: Final[int] = 8
_KEY_ID_HEX_LENGTH: Final[int] = _KEY_ID_BYTES * 2
_NONCE_BYTES: Final[int] = 12
_TAG_BYTES: Final[int] = 16


@dataclass(frozen=True, slots=True)
class _DBKey:
    key_id: str
    fernet: Fernet
    aesgcm: AESGCM


@dataclass(frozen=True, slots=True)
class DecryptedToken:
    """Plaintext of a stored value plus the format and key that sealed it."""

    plaintext: bytes
    algorithm: DBEncryptionAlgorithm
    key_id: str


def _hkdf(root: bytes, *, info: bytes, length: int) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=None, info=info).derive(
        root
    )


@lru_cache(maxsize=16)
def _load_key(key: str) -> _DBKey:
    """Parse a Fernet-format key and derive its AES-256-GCM key and key id.

    Raises:
        ValueError: If the key is not 32 urlsafe base64-encoded bytes.
    """
    fernet = Fernet(key)
    root = base64.urlsafe_b64decode(key)
    key_id = _hkdf(root, info=_KEY_ID_INFO, length=_KEY_ID_BYTES).hex()
    aes_key = _hkdf(root, info=_AES256GCM_KEY_INFO, length=32)
    return _DBKey(key_id=key_id, fernet=fernet, aesgcm=AESGCM(aes_key))


def _keyring(key: str) -> list[_DBKey]:
    """Return the current key followed by each distinct previous key."""
    keys = [_load_key(key)]
    for previous in config.TRACECAT__DB_ENCRYPTION_PREVIOUS_KEYS:
        previous_key = _load_key(previous)
        if all(k.key_id != previous_key.key_id for k in keys):
            keys.append(previous_key)
    return keys


def key_id(key: str) -> str:
    """Return the non-secret key id stamped into AES-256-GCM values."""
    return _load_key(key).key_id


def encrypt_token(plaintext: bytes, *, key: str) -> bytes:
    """Encrypt bytes with the configured write algorithm.

    Raises:
        ValueError: If the key is invalid.
    """
    db_key = _load_key(key)
    match config.TRACECAT__DB_ENCRYPTION_ALGORITHM:
        case DBEncryptionAlgorithm.FERNET:
            return db_key.fernet.encrypt(plaintext)
        case DBEncryptionAlgorithm.AES256GCM:
            header = AES256GCM_PREFIX + db_key.key_id.encode("ascii") + b":"
            # Random 96-bit nonces stay far below the GCM collision bound
            # (2^32 messages per key) at database write volumes.
            nonce = os.urandom(_NONCE_BYTES)
            sealed = db_key.aesgcm.encrypt(nonce, plaintext, header)
            return header + base64.urlsafe_b64encode(nonce + sealed)


def _decrypt_aes256gcm(token: bytes, keys: list[_DBKey]) -> DecryptedToken:
    key_id_end = token.find(b":", len(AES256GCM_PREFIX))
    if key_id_end - len(AES256GCM_PREFIX) != _KEY_ID_HEX_LENGTH:
        raise InvalidToken
    token_key_id = token[len(AES256GCM_PREFIX) : key_id_end].decode("ascii", "replace")
    db_key = next((k for k in keys if k.key_id == token_key_id), None)
    if db_key is None:
        raise InvalidToken

    header = token[: key_id_end + 1]
    try:
        raw = base64.b64decode(token[key_id_end + 1 :], altchars=b"-_", validate=True)
    except binascii.Error as e:
        raise InvalidToken from e
    if len(raw) < _NONCE_BYTES + _TAG_BYTES:
        raise InvalidToken

    nonce, sealed = raw[:_NONCE_BYTES], raw[_NONCE_BYTES:]
    try:
        plaintext = db_key.aesgcm.decrypt(nonce, sealed, header)
    except InvalidTag as e:
        raise InvalidToken from e
    return DecryptedToken(
        plaintext=plaintext,
        algorithm=DBEncryptionAlgorithm.AES256GCM,
        key_id=db_key.key_id,
    )


def decrypt_token(token: bytes, *, key: str) -> DecryptedToken:
    """Decrypt a stored value in either format, under the current or a previous key.

    Raises:
        ValueError: If a configured key is invalid.
        InvalidToken: If no configured key authenticates the value.
    """
    keys = _keyring(key)
    if token.startswith(AES256GCM_PREFIX):
        return _decrypt_aes256gcm(token, keys)
    for db_key in keys:
        try:
            plaintext = db_key.fernet.decrypt(token)
        except InvalidToken:
            continue
        return DecryptedToken(
            plaintext=plaintext,
            algorithm=DBEncryptionAlgorithm.FERNET,
            key_id=db_key.key_id,
        )
    raise InvalidToken


def is_current(decrypted: DecryptedToken, *, key: str) -> bool:
    """True when the value already uses the write algorithm and current key."""
    return (
        decrypted.algorithm == config.TRACECAT__DB_ENCRYPTION_ALGORITHM
        and decrypted.key_id == key_id(key)
    )


def encrypt_bytes(obj: dict[str, Any], *, key: str) -> bytes:
    obj_bytes = orjson.dumps(obj)
    return encrypt_token(obj_bytes, key=key)


def decrypt_bytes(encrypted_obj: bytes, *, key: str) -> dict[str, Any]:
    obj_bytes = decrypt_token(encrypted_obj, key=key).plaintext
    return orjson.loads(obj_bytes)


def decrypt_keyvalues(
    encrypted_keys: bytes, *, key: str, secret_type: SecretType = SecretType.CUSTOM
) -> list[SecretKeyValue]:
    obj = decrypt_bytes(encrypted_keys, key=key)
    keyvalues = SecretBase.factory(secret_type).model_validate(obj)
    return [SecretKeyValue(key=k, value=v) for k, v in keyvalues.model_dump().items()]


def encrypt_keyvalues(keyvalues: list[SecretKeyValue], *, key: str) -> bytes:
    obj = {kv.key: kv.value.get_secret_value() for kv in keyvalues}
    return encrypt_bytes(obj, key=key)


def encrypt_value(value: bytes, *, key: str) -> bytes:
    """Encrypt bytes with the configured write algorithm.

    Args:
        value: The bytes to encrypt
        key: The encryption key

    Returns:
        bytes: The ciphertext, ASCII-safe in both formats

    Raises:
        ValueError: If the key is invalid
    """
    try:
        return encrypt_token(value, key=key)
    except Exception as e:
        raise ValueError(f"Encryption failed: {str(e)}") from e


def decrypt_value(encrypted_value: bytes, *, key: str) -> bytes:
    """Decrypt a value stored in either format.

    Args:
        encrypted_value: The encrypted bytes
        key: The decryption key

    Returns:
        bytes: The decrypted value

    Raises:
        ValueError: If the key is invalid
        InvalidToken: If the encrypted data is corrupted
    """
    try:
        return decrypt_token(encrypted_value, key=key).plaintext
    except InvalidToken as e:
        raise InvalidToken("Decryption failed: corrupted or invalid token") from e
    except Exception as e:
        raise ValueError(f"Decryption failed: {str(e)}") from e


def is_set(value: bytes) -> bool:
    """True when ciphertext is not empty."""
    return bool(value)
