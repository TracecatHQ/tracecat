"""Run the public-API benchmark against a healthy Kubernetes deployment.

This adapter intentionally performs only read-only Kubernetes preflight checks.
It delegates fixture creation and load generation to the existing public-API
runner in ``--existing-deployment`` mode.
"""

from __future__ import annotations

import argparse
import asyncio
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from typing import Final

from . import runner

DEFAULT_CONTEXT: Final = "orbstack"
DEFAULT_NAMESPACE: Final = "tracecat"
DEFAULT_API_URL: Final = "https://tracecat.k8s.orb.local/api"
CORE_DEPLOYMENTS: Final = (
    "tracecat-api",
    "tracecat-worker",
    "tracecat-executor",
)
ORBSTACK_CA_NAME: Final = "OrbStack Development Root CA"


class KubernetesPreflightError(RuntimeError):
    """The selected Kubernetes deployment is not safe to benchmark."""


def _run_kubectl(arguments: Sequence[str]) -> str:
    command = ["kubectl", *arguments]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise KubernetesPreflightError(f"kubectl failed: {exc}") from exc
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
        raise KubernetesPreflightError(f"{' '.join(command)} failed: {detail}")
    return result.stdout.strip()


def _read_orbstack_ca() -> str:
    command = ["security", "find-certificate", "-c", ORBSTACK_CA_NAME, "-p"]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise KubernetesPreflightError(
            f"could not read the OrbStack CA from the macOS keychain: {exc}"
        ) from exc
    certificate = result.stdout.strip()
    if result.returncode != 0 or not certificate.startswith(
        "-----BEGIN CERTIFICATE-----"
    ):
        detail = result.stderr.strip() or "certificate was not found"
        raise KubernetesPreflightError(
            f"could not read the OrbStack CA from the macOS keychain: {detail}"
        )
    return certificate + "\n"


def verify_kubernetes_target(context: str, namespace: str) -> None:
    """Require the intended context and all load-bearing deployments."""
    current_context = _run_kubectl(("config", "current-context"))
    if current_context != context:
        raise KubernetesPreflightError(
            f"current Kubernetes context is {current_context!r}, expected {context!r}"
        )
    for deployment in CORE_DEPLOYMENTS:
        _run_kubectl(
            (
                "--context",
                context,
                "--namespace",
                namespace,
                "wait",
                "--for=condition=Available",
                f"deployment/{deployment}",
                "--timeout=1s",
            )
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tracecat-benchmark-kubernetes",
        description=(
            "Preflight a Kubernetes deployment, then run the benchmark through "
            "its public API with runner-only evidence."
        ),
    )
    parser.add_argument("--context", default=DEFAULT_CONTEXT)
    parser.add_argument("--namespace", default=DEFAULT_NAMESPACE)
    parser.add_argument(
        "runner_args",
        nargs=argparse.REMAINDER,
        help="Arguments forwarded to tracecat_benchmark.runner after '--'.",
    )
    return parser


def _prepare_runner_args(
    arguments: Sequence[str],
    *,
    default_api_url: str | None = DEFAULT_API_URL,
) -> list[str]:
    forwarded = list(arguments)
    if forwarded and forwarded[0] == "--":
        forwarded.pop(0)
    if not forwarded:
        raise KubernetesPreflightError(
            "runner arguments are required after '--'; use --help for examples"
        )
    if not any(
        argument == "--base-url" or argument.startswith("--base-url=")
        for argument in forwarded
    ):
        if default_api_url is None:
            raise KubernetesPreflightError(
                "--base-url is required when --context is not 'orbstack'"
            )
        forwarded[:0] = ["--base-url", default_api_url]
    if "--existing-deployment" not in forwarded:
        forwarded.append("--existing-deployment")
    return forwarded


async def amain(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    if not args.context or not args.namespace:
        print("--context and --namespace must be non-empty", file=sys.stderr)
        return 2
    try:
        forwarded = _prepare_runner_args(
            args.runner_args,
            default_api_url=(
                DEFAULT_API_URL if args.context == DEFAULT_CONTEXT else None
            ),
        )
        verify_kubernetes_target(args.context, args.namespace)
    except KubernetesPreflightError as exc:
        print(f"Kubernetes preflight error: {exc}", file=sys.stderr)
        return 2
    if args.context == DEFAULT_CONTEXT and not any(
        argument == "--tls-ca-file" or argument.startswith("--tls-ca-file=")
        for argument in forwarded
    ):
        try:
            certificate = _read_orbstack_ca()
        except KubernetesPreflightError as exc:
            print(f"Kubernetes preflight error: {exc}", file=sys.stderr)
            return 2
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix="tracecat-benchmark-orbstack-ca-",
            suffix=".pem",
        ) as ca_file:
            ca_file.write(certificate)
            ca_file.flush()
            return await runner.amain([*forwarded, "--tls-ca-file", ca_file.name])
    return await runner.amain(forwarded)


def main() -> None:
    raise SystemExit(asyncio.run(amain(sys.argv[1:])))


if __name__ == "__main__":
    main()
