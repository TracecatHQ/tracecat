import pytest

from tracecat.redis import connection


def test_redis_tls_kwargs_empty_without_ca(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(connection, "REDIS_SSL_CA_DATA", None)

    assert connection.redis_tls_kwargs("redis://localhost:6379") == {}


def test_redis_tls_kwargs_passes_ca_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ca_cert = "-----BEGIN CERTIFICATE-----\nexample\n-----END CERTIFICATE-----"
    monkeypatch.setattr(connection, "REDIS_SSL_CA_DATA", ca_cert)

    assert connection.redis_tls_kwargs("rediss://redis.example.com:6379") == {
        "ssl_ca_data": ca_cert
    }


def test_redis_tls_kwargs_requires_tls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(connection, "REDIS_SSL_CA_DATA", "certificate")

    with pytest.raises(
        ValueError, match="REDIS_SSL_CA_DATA requires a rediss:// Redis URL"
    ):
        connection.redis_tls_kwargs("redis://localhost:6379")
