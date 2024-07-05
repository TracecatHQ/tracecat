import os

from tracecat.secrets.encryption import decrypt_bytes, encrypt_bytes


def test_encrypt_decrypt_object(env_sandbox):
    key = os.getenv("TRACECAT__DB_ENCRYPTION_KEY")
    obj = {
        "client_id": "TEST_CLIENT_ID",
        "client_secret": "TEST_CLIENT_SECRET",
        "metadata": {"value": 1},
    }
    encrypted_obj = encrypt_bytes(obj, key=key)
    decrypted_obj = decrypt_bytes(encrypted_obj, key=key)
    assert decrypted_obj == obj
