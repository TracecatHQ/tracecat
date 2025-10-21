"""Utilities for working with Sigma rules using pySigma backends."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Annotated, Any, Literal

import yaml
from typing_extensions import Doc

from sigma.backends.elasticsearch.elasticsearch_eql import EqlBackend
from sigma.backends.elasticsearch.elasticsearch_esql import ESQLBackend
from sigma.backends.kusto.kusto import KustoBackend
from sigma.backends.splunk.splunk import SplunkBackend
from sigma.collection import SigmaCollection
from sigma.conversion.base import TextQueryBackend
from sigma.exceptions import SigmaError
from sigma.validation import SigmaValidator
from sigma.validators import core as validator_core

from tracecat.utils import to_jsonable_python
from tracecat_registry import registry


SigmaRuleInput = Annotated[
    str | dict[str, Any],
    Doc(
        "Sigma rule definition. Accepts a YAML string or a dictionary representation of the rule."
    ),
]


def _load_sigma_collection(sigma_rule: str | dict[str, Any]) -> SigmaCollection:
    if isinstance(sigma_rule, str):
        rule_payload = sigma_rule.strip()
        if not rule_payload:
            raise ValueError("Sigma rule cannot be empty.")
    else:
        if not sigma_rule:
            raise ValueError("Sigma rule dictionary cannot be empty.")
        try:
            rule_payload = yaml.safe_dump(sigma_rule)
        except yaml.YAMLError as exc:  # pragma: no cover - yaml edge cases
            raise ValueError(f"Failed to serialise Sigma rule dictionary: {exc}") from exc

    try:
        return SigmaCollection.from_yaml(rule_payload)
    except Exception as exc:  # pragma: no cover - pySigma raises heterogeneous errors
        raise ValueError(f"Failed to parse Sigma rule: {exc}") from exc


def _get_backend(identifier: str) -> type[TextQueryBackend]:
    backends: dict[str, type[TextQueryBackend]] = {
        "splunk": SplunkBackend,
        "kql": KustoBackend,
        "eql": EqlBackend,
        "esql": ESQLBackend,
    }

    try:
        backend_cls = backends[identifier]
    except KeyError as exc:  # pragma: no cover - defensive guard
        raise ValueError(
            f"Unsupported backend '{identifier}'. Supported values: {sorted(backends)}"
        ) from exc

    return backend_cls


@registry.register(
    default_title="Convert Sigma rule",
    description="Convert a Sigma rule into a SIEM query using pySigma backends.",
    display_group="Sigma",
    namespace="tools.sigma",
)
def convert_rule(
    sigma_rule: SigmaRuleInput,
    backend: Annotated[
        Literal["splunk", "kql", "eql", "esql"],
        Doc("Backend to use for conversion."),
    ] = "kql",
    output_format: Annotated[
        str | None,
        Doc(
            "Optional backend-specific output format identifier. Defaults to the backend's primary format."
        ),
    ] = None,
    backend_options: Annotated[
        dict[str, Any] | None,
        Doc("Optional keyword arguments forwarded to the backend constructor."),
    ] = None,
) -> Any:
    collection = _load_sigma_collection(sigma_rule)

    backend_cls = _get_backend(backend)
    try:
        backend_instance = (
            backend_cls(**backend_options) if backend_options else backend_cls()
        )
    except TypeError as exc:
        raise ValueError(f"Invalid backend options: {exc}") from exc

    try:
        raw_result = backend_instance.convert(collection, output_format=output_format)
    except SigmaError as exc:
        raise ValueError(f"Conversion failed: {exc}") from exc

    return to_jsonable_python(raw_result)


@registry.register(
    default_title="Lint Sigma rule",
    description="Run the default Sigma validators against a rule and return issues.",
    display_group="Sigma",
    namespace="tools.sigma",
)
def lint_rule(
    sigma_rule: SigmaRuleInput,
    validators: Annotated[
        Iterable[str] | None,
        Doc(
            "Validator identifiers to enable. Defaults to `['all']` to run the complete core validator suite."
        ),
    ] = None,
    exclusions: Annotated[
        Mapping[str, Iterable[str]] | None,
        Doc(
            "Optional mapping of rule IDs to validators that should be skipped for those rules."
        ),
    ] = None,
) -> Any:
    collection = _load_sigma_collection(sigma_rule)

    validator_config: dict[str, Any] = {"validators": list(validators or ["all"])}
    if exclusions:
        validator_config["exclusions"] = {
            rule_id: list(excluded)
            for rule_id, excluded in exclusions.items()
        }

    try:
        sigma_validator = SigmaValidator.from_dict(
            validator_config,
            validator_core.validators,
        )
    except SigmaError as exc:
        raise ValueError(f"Invalid validator configuration: {exc}") from exc

    issues = list(sigma_validator.validate_rules(iter(collection)))

    return to_jsonable_python(issues)
