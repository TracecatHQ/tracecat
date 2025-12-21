"""Core gRPC actions using runtime-compiled protobuf definitions.

Limitations and assumptions:
- Only unary-unary and unary-stream RPCs are supported (no client streaming).
- Protos are compiled at runtime using grpcio-tools; reflection is not used.
- Request payloads must be dicts that match the request message schema.

Example (server-streaming query with JSON payloads):
    proto = '''
    syntax = "proto3";
    package velociraptor;

    service API {
      rpc Query(VQLCollectorArgs) returns (stream VQLResponse);
    }

    message VQLCollectorArgs {
      string query = 1;
    }

    message VQLResponse {
      string json = 1;
    }
    '''

    responses = grpc_call(
        proto=proto,
        target="localhost:8001",
        service_name="API",
        method_name="Query",
        payload={"query": "SELECT * FROM pslist()"},
        timeout_seconds=30.0,
        ca_certificate=secrets.get_or_default("CA_CERTIFICATE"),
        client_cert=secrets.get_or_default("TLS_CERTIFICATE"),
        client_private_key=secrets.get_or_default("TLS_PRIVATE_KEY"),
        insecure=False,
    )

    # For streaming, iterate and decode response.json
    for response in responses:
        row = json.loads(response.json)
"""

from collections.abc import Iterator, Sequence
import importlib
from importlib import resources
from pathlib import Path
import sys
import tempfile
import uuid
from types import ModuleType
from typing import Annotated, Any

import grpc
from google.protobuf import descriptor as proto_descriptor
from google.protobuf import json_format
from google.protobuf import message_factory
from grpc_tools import protoc
from typing_extensions import Doc

from tracecat_registry import RegistrySecret, registry, secrets

mtls_secret = RegistrySecret(
    name="grpc_mtls",
    keys=["TLS_CERTIFICATE", "TLS_PRIVATE_KEY"],
    optional=True,
)
"""gRPC mTLS certificate secret.

- name: `grpc_mtls`
- keys:
    - `TLS_CERTIFICATE`
    - `TLS_PRIVATE_KEY`
"""

ca_cert_secret = RegistrySecret(
    name="grpc_ca_cert",
    keys=["CA_CERTIFICATE"],
    optional=True,
)
"""gRPC CA certificate secret.

- name: `grpc_ca_cert`
- keys:
    - `CA_CERTIFICATE`
"""


class ProtoCompilationError(RuntimeError):
    """Raised when protoc fails to compile a proto definition."""


GRPC_TOOLS_PROTO_INCLUDE = str(resources.files("grpc_tools") / "_proto")


def _compile_proto(
    proto_path: Path,
    *,
    include_paths: Sequence[str],
    output_dir: str,
) -> None:
    args = [
        "grpc_tools.protoc",
        *[f"-I{path}" for path in include_paths],
        f"--python_out={output_dir}",
        f"--grpc_python_out={output_dir}",
        str(proto_path),
    ]

    if protoc.main(args) != 0:
        raise ProtoCompilationError("Proto compilation failed.")


class ProtoLoader:
    """Compile a proto definition to Python modules and manage cleanup."""

    def __init__(
        self,
        proto: str,
    ) -> None:
        self._proto_source = proto
        self._temp_dir = tempfile.TemporaryDirectory()
        self._module_names: list[str] = []

    def load(self) -> tuple[ModuleType, ModuleType]:
        module_base = f"proto_{uuid.uuid4().hex}"
        proto_path = Path(self._temp_dir.name) / f"{module_base}.proto"
        proto_path.write_text(self._proto_source, encoding="utf-8")

        include_paths = [self._temp_dir.name, GRPC_TOOLS_PROTO_INCLUDE]

        _compile_proto(
            proto_path,
            include_paths=include_paths,
            output_dir=self._temp_dir.name,
        )

        sys.path.insert(0, self._temp_dir.name)
        try:
            proto_module = importlib.import_module(f"{module_base}_pb2")
            grpc_module = importlib.import_module(f"{module_base}_pb2_grpc")
        finally:
            try:
                sys.path.remove(self._temp_dir.name)
            except ValueError:
                pass
        self._module_names = [proto_module.__name__, grpc_module.__name__]
        return proto_module, grpc_module

    def cleanup(self) -> None:
        for name in self._module_names:
            sys.modules.pop(name, None)
        self._temp_dir.cleanup()


def _resolve_method_descriptor(
    proto_module: ModuleType,
    *,
    service_name: str,
    method_name: str,
) -> proto_descriptor.MethodDescriptor:
    try:
        service_descriptor = proto_module.DESCRIPTOR.services_by_name[service_name]
    except KeyError as exc:
        available = ", ".join(sorted(proto_module.DESCRIPTOR.services_by_name))
        raise ValueError(
            f"Service '{service_name}' not found in proto. "
            f"Available services: {available or 'none'}."
        ) from exc

    try:
        return service_descriptor.methods_by_name[method_name]
    except KeyError as exc:
        available = ", ".join(sorted(service_descriptor.methods_by_name))
        raise ValueError(
            f"Method '{service_name}.{method_name}' not found in proto. "
            f"Available methods: {available or 'none'}."
        ) from exc


def _message_class_for_descriptor(
    message_descriptor: proto_descriptor.Descriptor,
) -> type[Any]:
    return message_factory.GetMessageClass(message_descriptor)


def build_channel(
    target: str,
    *,
    ca_certificate: str | None,
    client_private_key: str | None,
    client_cert: str | None,
    insecure: bool,
) -> grpc.Channel:
    if insecure:
        return grpc.insecure_channel(target)

    credentials = grpc.ssl_channel_credentials(
        root_certificates=ca_certificate.encode("utf-8") if ca_certificate else None,
        private_key=client_private_key.encode("utf-8") if client_private_key else None,
        certificate_chain=client_cert.encode("utf-8") if client_cert else None,
    )
    return grpc.secure_channel(target, credentials)


def grpc_call(
    *,
    proto: str,
    target: str,
    service_name: str,
    method_name: str,
    payload: dict[str, Any] | None,
    timeout_seconds: float | None,
    ca_certificate: str | None,
    client_private_key: str | None,
    client_cert: str | None,
    insecure: bool,
) -> Any | Iterator[Any]:
    """Invoke a gRPC method dynamically and return a response or response iterator."""
    loader = ProtoLoader(
        proto,
    )
    channel: grpc.Channel | None = None
    is_streaming = False

    try:
        proto_module, grpc_module = loader.load()

        stub_class = getattr(grpc_module, f"{service_name}Stub")

        channel = build_channel(
            target,
            ca_certificate=ca_certificate,
            client_private_key=client_private_key,
            client_cert=client_cert,
            insecure=insecure,
        )
        stub = stub_class(channel)
        method = getattr(stub, method_name)
        method_descriptor = _resolve_method_descriptor(
            proto_module,
            service_name=service_name,
            method_name=method_name,
        )
        request_class = _message_class_for_descriptor(method_descriptor.input_type)
        request = request_class()
        if payload is not None:
            json_format.ParseDict(payload, request)

        if isinstance(method, grpc.UnaryUnaryMultiCallable):
            return method(request, timeout=timeout_seconds)

        if isinstance(method, grpc.UnaryStreamMultiCallable):
            is_streaming = True

            def stream() -> Iterator[Any]:
                try:
                    yield from method(request, timeout=timeout_seconds)
                finally:
                    if channel:
                        channel.close()
                    loader.cleanup()

            return stream()

        if isinstance(
            method,
            (grpc.StreamUnaryMultiCallable, grpc.StreamStreamMultiCallable),
        ):
            raise ValueError("Client-streaming RPCs are not supported.")

        raise ValueError(
            f"Unsupported gRPC method type for '{service_name}.{method_name}'."
        )
    finally:
        if not is_streaming:
            if channel:
                channel.close()
            loader.cleanup()


@registry.register(
    namespace="core.grpc",
    description="Call a gRPC method using a runtime-compiled proto definition.",
    default_title="gRPC request",
    display_group="gRPC",
    secrets=[mtls_secret, ca_cert_secret],
)
def request(
    *,
    target: Annotated[str, Doc("gRPC server address in host:port format.")],
    service_name: Annotated[str, Doc("gRPC service name (e.g., 'API').")],
    method_name: Annotated[str, Doc("gRPC method name (e.g., 'Query').")],
    proto: Annotated[
        str,
        Doc("Inline .proto definition."),
    ],
    payload: Annotated[
        dict[str, Any] | None,
        Doc("Request payload as a dict matching the protobuf schema."),
    ] = None,
    timeout_seconds: Annotated[
        float | None,
        Doc("Timeout in seconds for the RPC. Defaults to no timeout."),
    ] = None,
    insecure: Annotated[
        bool,
        Doc("Use an insecure plaintext channel. Defaults to False (TLS channel)."),
    ] = False,
) -> dict[str, Any] | list[dict[str, Any]]:
    """Call a gRPC method using a runtime-compiled protobuf definition."""
    client_cert = secrets.get_or_default("TLS_CERTIFICATE")
    client_private_key = secrets.get_or_default("TLS_PRIVATE_KEY")
    ca_certificate = secrets.get_or_default("CA_CERTIFICATE")

    result = grpc_call(
        proto=proto,
        target=target,
        service_name=service_name,
        method_name=method_name,
        payload=payload,
        timeout_seconds=timeout_seconds,
        ca_certificate=ca_certificate,
        client_private_key=client_private_key,
        client_cert=client_cert,
        insecure=insecure,
    )

    if isinstance(result, Iterator):
        return [
            json_format.MessageToDict(
                item,
                preserving_proto_field_name=True,
            )
            for item in result
        ]
    return json_format.MessageToDict(
        result,
        preserving_proto_field_name=True,
    )
