from typing import Any

import orjson
from cryptography.fernet import Fernet, InvalidToken

from .models import SecretBase, SecretKeyValue


def encrypt_bytes(obj: dict[str, Any], *, key: str) -> bytes:
    cipher_suite = Fernet(key)
    obj_bytes = orjson.dumps(obj)
    encrypted_value = cipher_suite.encrypt(obj_bytes)
    return encrypted_value


def decrypt_bytes(encrypted_obj: bytes, *, key: str) -> dict[str, Any]:
    cipher_suite = Fernet(key)
    obj_bytes = cipher_suite.decrypt(encrypted_obj)
    return orjson.loads(obj_bytes)


def decrypt_keyvalues(
    encrypted_keys: bytes, *, key: str, secret_type: str = "custom"
) -> list[SecretKeyValue]:
    obj = decrypt_bytes(encrypted_keys, key=key)
    keyvalues = SecretBase.factory(secret_type).model_validate(obj)
    return [SecretKeyValue(key=k, value=v) for k, v in keyvalues.model_dump().items()]


def encrypt_keyvalues(keyvalues: list[SecretKeyValue], *, key: str) -> bytes:
    obj = {kv.key: kv.value.get_secret_value() for kv in keyvalues}
    return encrypt_bytes(obj, key=key)


def encrypt_value(value: bytes, *, key: str) -> bytes:
    """Encrypt a string using Fernet encryption.

    Args:
        value: The string to encrypt
        key: The encryption key

    Returns:
        str: The encrypted value as a base64-encoded string

    Raises:
        ValueError: If the key is invalid
    """
    try:
        return Fernet(key).encrypt(value)
    except Exception as e:
        raise ValueError(f"Encryption failed: {str(e)}") from e


def decrypt_value(encrypted_value: bytes, *, key: str) -> bytes:
    """Decrypt a Fernet-encrypted value back to a string.

    Args:
        encrypted_value: The encrypted bytes
        key: The decryption key

    Returns:
        str: The decrypted string value

    Raises:
        ValueError: If the key is invalid
        InvalidToken: If the encrypted data is corrupted
    """
    try:
        return Fernet(key).decrypt(encrypted_value)
    except InvalidToken as e:
        raise InvalidToken("Decryption failed: corrupted or invalid token") from e
    except Exception as e:
        raise ValueError(f"Decryption failed: {str(e)}") from e
