"""AES-256-GCM database encryption, Fernet compatibility, and key rotation."""

import base64
import importlib
import string
from collections.abc import Iterator

import pytest
from cryptography.fernet import Fernet, InvalidToken

from tracecat import config
from tracecat.config import DBEncryptionAlgorithm
from tracecat.secrets.encryption import (
    AES256GCM_PREFIX,
    decrypt_bytes,
    decrypt_token,
    decrypt_value,
    encrypt_bytes,
    encrypt_value,
    is_current,
    key_id,
)


@pytest.fixture
def key() -> str:
    return Fernet.generate_key().decode()


@pytest.fixture
def aes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        config, "TRACECAT__DB_ENCRYPTION_ALGORITHM", DBEncryptionAlgorithm.AES256GCM
    )


@pytest.fixture(autouse=True)
def fernet_writes_no_previous_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        config, "TRACECAT__DB_ENCRYPTION_ALGORITHM", DBEncryptionAlgorithm.FERNET
    )
    monkeypatch.setattr(config, "TRACECAT__DB_ENCRYPTION_PREVIOUS_KEYS", ())


def _set_previous_keys(monkeypatch: pytest.MonkeyPatch, *keys: str) -> None:
    monkeypatch.setattr(config, "TRACECAT__DB_ENCRYPTION_PREVIOUS_KEYS", keys)


def _flip_last_body_byte(token: bytes) -> bytes:
    header_end = token.rfind(b":") + 1
    raw = bytearray(base64.urlsafe_b64decode(token[header_end:]))
    raw[-1] ^= 0x01
    return token[:header_end] + base64.urlsafe_b64encode(bytes(raw))


@pytest.mark.parametrize("algorithm", list(DBEncryptionAlgorithm))
def test_round_trip(
    monkeypatch: pytest.MonkeyPatch, key: str, algorithm: DBEncryptionAlgorithm
) -> None:
    """Both write algorithms round-trip through the value and object APIs."""
    monkeypatch.setattr(config, "TRACECAT__DB_ENCRYPTION_ALGORITHM", algorithm)

    assert decrypt_value(encrypt_value(b"secret", key=key), key=key) == b"secret"
    obj = {"api_key": "synthetic-value", "nested": {"n": 1}}
    assert decrypt_bytes(encrypt_bytes(obj, key=key), key=key) == obj


def test_fernet_writes_keep_fernet_format(key: str) -> None:
    """The default still writes plain Fernet tokens."""
    token = encrypt_value(b"secret", key=key)

    assert token.startswith(b"gAAAAA")
    assert Fernet(key).decrypt(token) == b"secret"


@pytest.mark.usefixtures("aes")
def test_aes_format(key: str) -> None:
    """AES writes are ``tc1:<16 hex key id>:<urlsafe base64>``, ASCII-safe for text columns."""
    token = encrypt_value(b"secret", key=key)

    prefix, kid, body = token.split(b":")
    assert prefix + b":" == AES256GCM_PREFIX
    assert kid.decode() == key_id(key)
    assert len(kid) == 16 and set(kid.decode()) <= set(string.hexdigits.lower())
    # 12-byte nonce + plaintext + 16-byte tag.
    assert len(base64.urlsafe_b64decode(body)) == 12 + len(b"secret") + 16
    token.decode("ascii")


@pytest.mark.usefixtures("aes")
def test_aes_nonce_is_fresh_per_write(key: str) -> None:
    """Encrypting the same plaintext twice never reuses a nonce."""
    bodies = {encrypt_value(b"same", key=key).split(b":")[2] for _ in range(64)}
    nonces = {base64.urlsafe_b64decode(body)[:12] for body in bodies}

    assert len(nonces) == 64


@pytest.mark.usefixtures("aes")
def test_existing_fernet_rows_decrypt_after_switching_to_aes(key: str) -> None:
    """Switching writes to AES leaves every existing Fernet row readable."""
    legacy = Fernet(key).encrypt(b'{"a": 1}')

    assert decrypt_value(legacy, key=key) == b'{"a": 1}'
    assert decrypt_bytes(legacy, key=key) == {"a": 1}


def test_aes_rows_decrypt_after_switching_back_to_fernet(
    monkeypatch: pytest.MonkeyPatch, key: str
) -> None:
    """Reads accept AES rows whatever the write setting is."""
    monkeypatch.setattr(
        config, "TRACECAT__DB_ENCRYPTION_ALGORITHM", DBEncryptionAlgorithm.AES256GCM
    )
    token = encrypt_value(b"secret", key=key)
    monkeypatch.setattr(
        config, "TRACECAT__DB_ENCRYPTION_ALGORITHM", DBEncryptionAlgorithm.FERNET
    )

    assert decrypt_value(token, key=key) == b"secret"


@pytest.mark.usefixtures("aes")
@pytest.mark.parametrize(
    "tamper",
    [
        pytest.param(_flip_last_body_byte, id="flipped_tag_byte"),
        pytest.param(lambda t: t[:-8], id="truncated"),
        pytest.param(lambda t: t[: t.rfind(b":") + 1] + b"AAAA", id="too_short"),
        pytest.param(lambda t: t + b"!", id="non_base64"),
        pytest.param(lambda t: t.replace(b":", b"", 1), id="missing_key_id_separator"),
        pytest.param(lambda _: AES256GCM_PREFIX, id="prefix_only"),
        pytest.param(
            lambda t: AES256GCM_PREFIX + b"0" * 16 + t[t.rfind(b":") :],
            id="unknown_key_id",
        ),
        pytest.param(
            lambda t: AES256GCM_PREFIX + b"0" * 15 + t[t.rfind(b":") :],
            id="short_key_id",
        ),
    ],
)
def test_tampered_aes_raises_invalid_token(key: str, tamper) -> None:
    """Every malformed or modified AES value raises InvalidToken, as Fernet does."""
    token = tamper(encrypt_value(b"secret", key=key))

    with pytest.raises(InvalidToken):
        decrypt_value(token, key=key)
    with pytest.raises(InvalidToken):
        decrypt_bytes(token, key=key)


@pytest.mark.usefixtures("aes")
def test_key_id_is_authenticated(key: str) -> None:
    """Rewriting the key id in the header fails authentication, not just lookup."""
    other = Fernet.generate_key().decode()
    token = encrypt_value(b"secret", key=key)
    forged = token.replace(key_id(key).encode(), key_id(other).encode())

    with pytest.raises(InvalidToken):
        decrypt_value(forged, key=other)


@pytest.mark.parametrize("algorithm", list(DBEncryptionAlgorithm))
def test_wrong_key_raises_invalid_token(
    monkeypatch: pytest.MonkeyPatch, key: str, algorithm: DBEncryptionAlgorithm
) -> None:
    monkeypatch.setattr(config, "TRACECAT__DB_ENCRYPTION_ALGORITHM", algorithm)
    token = encrypt_value(b"secret", key=key)
    other = Fernet.generate_key().decode()

    with pytest.raises(InvalidToken):
        decrypt_value(token, key=other)
    with pytest.raises(InvalidToken):
        decrypt_bytes(encrypt_bytes({"a": 1}, key=key), key=other)


@pytest.mark.parametrize("algorithm", list(DBEncryptionAlgorithm))
def test_malformed_key_raises_value_error(
    monkeypatch: pytest.MonkeyPatch, key: str, algorithm: DBEncryptionAlgorithm
) -> None:
    """A misconfigured key stays a ValueError, distinct from a bad ciphertext."""
    monkeypatch.setattr(config, "TRACECAT__DB_ENCRYPTION_ALGORITHM", algorithm)
    token = encrypt_value(b"secret", key=key)

    with pytest.raises(ValueError):
        decrypt_value(token, key="invalid_key")
    with pytest.raises(ValueError):
        encrypt_value(b"secret", key="invalid_key")


def test_prefix_cannot_collide_with_fernet(key: str) -> None:
    """``:`` is outside the urlsafe base64 alphabet, so Fernet tokens never carry the prefix."""
    urlsafe_alphabet = set(string.ascii_letters + string.digits + "-_=")

    assert ":" not in urlsafe_alphabet
    assert not set(AES256GCM_PREFIX.decode()) <= urlsafe_alphabet
    for _ in range(32):
        assert not Fernet(key).encrypt(b"x").startswith(AES256GCM_PREFIX)


def test_key_id_is_stable_and_distinct(key: str) -> None:
    other = Fernet.generate_key().decode()

    assert key_id(key) == key_id(key)
    assert key_id(key) != key_id(other)
    assert key_id(key) not in base64.urlsafe_b64decode(key).hex()


@pytest.mark.parametrize("algorithm", list(DBEncryptionAlgorithm))
def test_previous_keys_decrypt_during_rotation(
    monkeypatch: pytest.MonkeyPatch, key: str, algorithm: DBEncryptionAlgorithm
) -> None:
    """Values sealed under a retired key decrypt once it is listed as a previous key."""
    monkeypatch.setattr(config, "TRACECAT__DB_ENCRYPTION_ALGORITHM", algorithm)
    old_key = key
    new_key = Fernet.generate_key().decode()
    token = encrypt_value(b"secret", key=old_key)

    with pytest.raises(InvalidToken):
        decrypt_value(token, key=new_key)

    _set_previous_keys(monkeypatch, old_key)
    decrypted = decrypt_token(token, key=new_key)
    assert decrypted.plaintext == b"secret"
    assert decrypted.algorithm == algorithm
    assert decrypted.key_id == key_id(old_key)
    assert not is_current(decrypted, key=new_key)

    resealed = encrypt_value(decrypted.plaintext, key=new_key)
    assert is_current(decrypt_token(resealed, key=new_key), key=new_key)

    _set_previous_keys(monkeypatch)
    assert decrypt_value(resealed, key=new_key) == b"secret"


def test_is_current_tracks_write_algorithm(
    monkeypatch: pytest.MonkeyPatch, key: str
) -> None:
    decrypted = decrypt_token(encrypt_value(b"secret", key=key), key=key)
    assert is_current(decrypted, key=key)

    monkeypatch.setattr(
        config, "TRACECAT__DB_ENCRYPTION_ALGORITHM", DBEncryptionAlgorithm.AES256GCM
    )
    assert not is_current(decrypted, key=key)


class TestConfig:
    """Startup parsing of the algorithm and previous keys."""

    @pytest.fixture
    def env(self) -> Iterator[pytest.MonkeyPatch]:
        """Env changes that are undone before config is reloaded back to normal."""
        with pytest.MonkeyPatch.context() as mp:
            yield mp
        importlib.reload(config)

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            (None, DBEncryptionAlgorithm.FERNET),
            ("", DBEncryptionAlgorithm.FERNET),
            ("fernet", DBEncryptionAlgorithm.FERNET),
            (" AES256GCM ", DBEncryptionAlgorithm.AES256GCM),
        ],
    )
    def test_algorithm_parsing(
        self,
        env: pytest.MonkeyPatch,
        raw: str | None,
        expected: DBEncryptionAlgorithm,
    ) -> None:
        if raw is None:
            env.delenv("TRACECAT__DB_ENCRYPTION_ALGORITHM", raising=False)
        else:
            env.setenv("TRACECAT__DB_ENCRYPTION_ALGORITHM", raw)

        assert importlib.reload(config).TRACECAT__DB_ENCRYPTION_ALGORITHM == expected

    @pytest.mark.parametrize("raw", ["aes", "aes128", "none"])
    def test_unknown_algorithm_fails_at_startup(
        self, env: pytest.MonkeyPatch, raw: str
    ) -> None:
        env.setenv("TRACECAT__DB_ENCRYPTION_ALGORITHM", raw)

        with pytest.raises(ValueError, match="TRACECAT__DB_ENCRYPTION_ALGORITHM"):
            importlib.reload(config)

    def test_previous_keys_parsing(self, env: pytest.MonkeyPatch, key: str) -> None:
        other = Fernet.generate_key().decode()
        env.setenv("TRACECAT__DB_ENCRYPTION_PREVIOUS_KEYS", f" {key}, ,{other},")

        assert importlib.reload(config).TRACECAT__DB_ENCRYPTION_PREVIOUS_KEYS == (
            key,
            other,
        )

    def test_invalid_previous_key_fails_without_echoing_it(
        self, env: pytest.MonkeyPatch, key: str
    ) -> None:
        bad = "not-a-valid-key-but-looks-secret"
        env.setenv("TRACECAT__DB_ENCRYPTION_PREVIOUS_KEYS", f"{key},{bad}")

        with pytest.raises(ValueError, match="entry 1") as exc_info:
            importlib.reload(config)
        assert bad not in str(exc_info.value)
        assert key not in str(exc_info.value)
