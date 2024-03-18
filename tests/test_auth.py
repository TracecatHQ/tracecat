import os

import pytest
from cryptography.fernet import Fernet

from tracecat.auth import decrypt_key, encrypt_key


@pytest.fixture(autouse=True)
def setup_env():
    os.environ["TRACECAT__DB_ENCRYPTION_KEY"] = Fernet.generate_key().decode()
    yield


def test_encrypt_decrypt():
    api_key = "mock_api_key"
    encrypted_api_key = encrypt_key(api_key)
    decrypted_api_key = decrypt_key(encrypted_api_key)
    assert decrypted_api_key == api_key
