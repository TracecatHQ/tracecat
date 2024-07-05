from typing import Any

import orjson
from cryptography.fernet import Fernet

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
