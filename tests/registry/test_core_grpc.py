"""Integration tests for dynamic gRPC client actions."""

import ipaddress
import sys
import uuid
from concurrent import futures
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import grpc
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
)
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from google.protobuf import descriptor_pb2, descriptor_pool, message_factory
from grpc_tools import protoc
from tracecat_registry._internal import secrets as registry_secrets
from tracecat_registry.core import grpc as grpc_core
from tracecat_registry.core.grpc import request as grpc_request


@contextmanager
def registry_secrets_sandbox(secrets: dict[str, str]):
    """Context manager that sets up the registry secrets context."""
    token = registry_secrets.set_context(secrets)
    try:
        yield
    finally:
        registry_secrets.reset_context(token)


def _proto_source(package_name: str) -> str:
    return (
        'syntax = "proto3";\n'
        f"package {package_name};\n"
        "\n"
        "service TestService {\n"
        "  rpc Echo(EchoRequest) returns (EchoReply);\n"
        "  rpc StreamEcho(EchoRequest) returns (stream EchoReply);\n"
        "}\n"
        "\n"
        "message EchoRequest {\n"
        "  string message = 1;\n"
        "}\n"
        "\n"
        "message EchoReply {\n"
        "  string message = 1;\n"
        "}\n"
    )


def _compile_descriptor_set(tmp_path: Path, proto_source: str) -> Path:
    proto_path = tmp_path / "service.proto"
    proto_path.write_text(proto_source, encoding="utf-8")
    descriptor_path = tmp_path / "service.pb"

    result = protoc.main(
        [
            "grpc_tools.protoc",
            f"-I{tmp_path}",
            f"--descriptor_set_out={descriptor_path}",
            "--include_imports",
            str(proto_path),
        ]
    )
    if result != 0:
        raise RuntimeError(f"protoc failed with exit code {result}")
    return descriptor_path


def _load_message_types(
    descriptor_path: Path,
    package_name: str,
) -> tuple[type, type]:
    file_set = descriptor_pb2.FileDescriptorSet.FromString(descriptor_path.read_bytes())
    pool = descriptor_pool.DescriptorPool()
    for fd in file_set.file:
        pool.Add(fd)
    request_type = pool.FindMessageTypeByName(f"{package_name}.EchoRequest")
    reply_type = pool.FindMessageTypeByName(f"{package_name}.EchoReply")
    return (
        message_factory.GetMessageClass(request_type),
        message_factory.GetMessageClass(reply_type),
    )


@pytest.fixture
def grpc_package_name() -> str:
    return f"test_{uuid.uuid4().hex}"


@pytest.fixture
def grpc_proto_source(grpc_package_name: str) -> str:
    return _proto_source(grpc_package_name)


@pytest.fixture
def grpc_message_types(
    tmp_path: Path,
    grpc_proto_source: str,
    grpc_package_name: str,
) -> tuple[type, type]:
    descriptor_path = _compile_descriptor_set(tmp_path, grpc_proto_source)
    return _load_message_types(descriptor_path, grpc_package_name)


def _build_generic_handler(
    package_name: str,
    echo_request_type: type,
    echo_reply_type: type,
) -> grpc.GenericRpcHandler:
    def echo(request, context):
        return echo_reply_type(message=request.message)

    def stream_echo(request, context):
        for idx in range(3):
            yield echo_reply_type(message=f"{request.message}-{idx}")

    return grpc.method_handlers_generic_handler(
        f"{package_name}.TestService",
        {
            "Echo": grpc.unary_unary_rpc_method_handler(
                echo,
                request_deserializer=echo_request_type.FromString,
                response_serializer=echo_reply_type.SerializeToString,
            ),
            "StreamEcho": grpc.unary_stream_rpc_method_handler(
                stream_echo,
                request_deserializer=echo_request_type.FromString,
                response_serializer=echo_reply_type.SerializeToString,
            ),
        },
    )


@pytest.fixture
def grpc_server(
    grpc_package_name: str,
    grpc_message_types: tuple[type, type],
):
    echo_request_type, echo_reply_type = grpc_message_types
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=2))
    server.add_generic_rpc_handlers(
        (
            _build_generic_handler(
                grpc_package_name,
                echo_request_type,
                echo_reply_type,
            ),
        )
    )
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    try:
        yield f"127.0.0.1:{port}"
    finally:
        server.stop(0).wait()


def _pem_private_key(key) -> str:
    return (
        key.private_bytes(
            encoding=Encoding.PEM,
            format=PrivateFormat.PKCS8,
            encryption_algorithm=NoEncryption(),
        )
        .decode("utf-8")
        .rstrip("\n")
    )


def _pem_certificate(cert) -> str:
    return cert.public_bytes(Encoding.PEM).decode("utf-8").rstrip("\n")


def _create_ca() -> tuple[rsa.RSAPrivateKey, x509.Certificate]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, "tracecat-test-ca")]
    )
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(UTC) - timedelta(days=1))
        .not_valid_after(datetime.now(UTC) + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    return key, cert


def _create_cert(
    *,
    ca_key: rsa.RSAPrivateKey,
    ca_cert: x509.Certificate,
    common_name: str,
    san_dns: str | None = None,
    san_ip: ipaddress.IPv4Address | None = None,
    client_auth: bool = False,
) -> tuple[rsa.RSAPrivateKey, x509.Certificate]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(UTC) - timedelta(days=1))
        .not_valid_after(datetime.now(UTC) + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
    )

    san_entries: list[x509.GeneralName] = []
    if san_dns:
        san_entries.append(x509.DNSName(san_dns))
    if san_ip:
        san_entries.append(x509.IPAddress(san_ip))
    if san_entries:
        builder = builder.add_extension(
            x509.SubjectAlternativeName(san_entries),
            critical=False,
        )

    usage = (
        ExtendedKeyUsageOID.CLIENT_AUTH
        if client_auth
        else ExtendedKeyUsageOID.SERVER_AUTH
    )
    builder = builder.add_extension(
        x509.ExtendedKeyUsage([usage]),
        critical=False,
    )

    cert = builder.sign(ca_key, hashes.SHA256())
    return key, cert


def _generate_tls_material() -> tuple[str, str, str, str, str]:
    ca_key, ca_cert = _create_ca()
    server_key, server_cert = _create_cert(
        ca_key=ca_key,
        ca_cert=ca_cert,
        common_name="localhost",
        san_dns="localhost",
        san_ip=ipaddress.IPv4Address("127.0.0.1"),
        client_auth=False,
    )
    client_key, client_cert = _create_cert(
        ca_key=ca_key,
        ca_cert=ca_cert,
        common_name="tracecat-client",
        client_auth=True,
    )

    return (
        _pem_certificate(ca_cert),
        _pem_certificate(server_cert),
        _pem_private_key(server_key),
        _pem_certificate(client_cert),
        _pem_private_key(client_key),
    )


def test_grpc_request_unary(grpc_server, grpc_proto_source: str) -> None:
    result = grpc_request(
        target=grpc_server,
        service_name="TestService",
        method_name="Echo",
        payload={"message": "hello"},
        proto=grpc_proto_source,
        insecure=True,
    )
    assert result == {"message": "hello"}


def test_grpc_request_streaming(grpc_server, grpc_proto_source: str) -> None:
    result = grpc_request(
        target=grpc_server,
        service_name="TestService",
        method_name="StreamEcho",
        payload={"message": "hi"},
        proto=grpc_proto_source,
        insecure=True,
    )
    assert result == [
        {"message": "hi-0"},
        {"message": "hi-1"},
        {"message": "hi-2"},
    ]


def test_grpc_request_invalid_service(grpc_proto_source: str) -> None:
    with pytest.raises(AttributeError, match="MissingStub"):
        grpc_request(
            target="127.0.0.1:50051",
            service_name="Missing",
            method_name="Echo",
            payload={"message": "hello"},
            proto=grpc_proto_source,
            insecure=True,
        )


def test_proto_loader_sys_path_cleanup_is_robust(tmp_path: Path) -> None:
    """Ensure ProtoLoader cleanup removes the correct sys.path entry.

    Regression test: sys.path.pop(0) could remove the wrong entry if sys.path
    changed during module import.
    """
    loader = grpc_core.ProtoLoader(_proto_source(f"pkg_{uuid.uuid4().hex}"))
    sentinel = str(tmp_path / "sentinel-path")

    original_import_module = grpc_core.importlib.import_module
    inserted = False

    def _import_module_with_sys_path_mutation(name: str, package: str | None = None):
        nonlocal inserted
        # Mutate sys.path after ProtoLoader has inserted its temp dir at index 0,
        # but before the finally block runs, simulating concurrent modification.
        if (
            not inserted
            and name.endswith("_pb2")
            and sys.path
            and sys.path[0] == loader._temp_dir.name
        ):
            sys.path.insert(0, sentinel)
            inserted = True
        return original_import_module(name, package=package)

    grpc_core.importlib.import_module = _import_module_with_sys_path_mutation
    try:
        loader.load()
        assert inserted is True
        assert loader._temp_dir.name not in sys.path
        assert sys.path[0] == sentinel
    finally:
        grpc_core.importlib.import_module = original_import_module
        while sentinel in sys.path:
            sys.path.remove(sentinel)
        loader.cleanup()


@pytest.mark.integration
def test_grpc_request_mtls(
    grpc_message_types: tuple[type, type],
    grpc_package_name: str,
    grpc_proto_source: str,
) -> None:
    echo_request_type, echo_reply_type = grpc_message_types
    ca_cert, server_cert, server_key, client_cert, client_key = _generate_tls_material()

    server_credentials = grpc.ssl_server_credentials(
        [(server_key.encode("utf-8"), server_cert.encode("utf-8"))],
        root_certificates=ca_cert.encode("utf-8"),
        require_client_auth=True,
    )

    server = grpc.server(futures.ThreadPoolExecutor(max_workers=2))
    server.add_generic_rpc_handlers(
        (
            _build_generic_handler(
                grpc_package_name,
                echo_request_type,
                echo_reply_type,
            ),
        )
    )
    port = server.add_secure_port("127.0.0.1:0", server_credentials)
    server.start()

    try:
        with registry_secrets_sandbox(
            {
                "TLS_CERTIFICATE": client_cert,
                "TLS_PRIVATE_KEY": client_key,
                "CA_CERTIFICATE": ca_cert,
            }
        ):
            result = grpc_request(
                target=f"127.0.0.1:{port}",
                service_name="TestService",
                method_name="Echo",
                payload={"message": "secure"},
                proto=grpc_proto_source,
            )
        assert result == {"message": "secure"}
    finally:
        server.stop(0).wait()
