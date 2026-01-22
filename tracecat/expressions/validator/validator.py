import re
from collections.abc import Iterator, Mapping
from types import TracebackType
from typing import Any, Literal, Self, TypeVar, override

from lark import Token, Tree
from pydantic import BaseModel, Field

from tracecat.concurrency import GatheringTaskGroup
from tracecat.dsl.schemas import TaskResult
from tracecat.expressions.common import MAX_VARS_PATH_DEPTH, ExprContext, ExprType
from tracecat.expressions.expectations import ExpectedField
from tracecat.expressions.validator.base import BaseExprValidator
from tracecat.integrations.enums import OAuthGrantType
from tracecat.integrations.schemas import ProviderKey
from tracecat.integrations.service import IntegrationService
from tracecat.logger import logger
from tracecat.secrets.schemas import SecretSearch
from tracecat.secrets.service import SecretsService
from tracecat.validation.schemas import (
    TemplateActionExprValidationResult,
    ValidationDetail,
)
from tracecat.variables.schemas import VariableSearch
from tracecat.variables.service import VariablesService

T = TypeVar("T")


class ExprValidationContext(BaseModel):
    """Container for the validation context of an expression tree."""

    action_refs: set[str]
    trigger_context: Any = Field(default_factory=dict)


class ExprValidator(BaseExprValidator[ValidationDetail]):
    """Expression validator for workflow actions."""

    _visitor_name = "ExprValidator"
    _expr_kind = "Workflow Actions"

    def __init__(
        self,
        validation_context: ExprValidationContext,
        keep_success: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._context = validation_context
        self._task_group = GatheringTaskGroup()
        self._validation_details: list[ValidationDetail] = []
        self._keep_success = keep_success

    async def __aenter__(self) -> Self:
        """Initialize the validator with a task group."""
        await self._task_group.__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Clean up the task group."""
        await self._task_group.__aexit__(exc_type, exc_val, exc_tb)

    async def _secret_validator(
        self, *, name: str, key: str, loc: tuple[str | int, ...], environment: str
    ) -> None:
        # (1) Check if the secret is defined
        async with SecretsService.with_session() as service:
            defined_secret = await service.search_secrets(
                SecretSearch(names={name}, environment=environment)
            )
            logger.info("Secret search results", defined_secret=defined_secret)
            if (n_found := len(defined_secret)) != 1:
                logger.debug(
                    "Secret not found in SECRET context usage",
                    n_found=n_found,
                    secret_name=name,
                    environment=environment,
                )
                return self.add(
                    status="error",
                    msg=f"Found {n_found} secrets matching {name!r} in the {environment!r} environment.",
                    type=ExprType.SECRET,
                    loc=("expression", f"{ExprContext.SECRETS.value}.{name}.{key}"),
                )
            # There should only be 1 secret
            decrypted_keys = service.decrypt_keys(defined_secret[0].encrypted_keys)
            defined_keys = {kv.key for kv in decrypted_keys}

        # (2) Check if the secret has the correct keys
        if key not in defined_keys:
            logger.error(
                "Missing secret keys in SECRET context usage",
                secret_name=name,
                missing_key=key,
            )
            return self.add(
                status="error",
                msg=f"Secret {name!r} is missing key: {key!r}",
                type=ExprType.SECRET,
                loc=("expression", f"{ExprContext.SECRETS.value}.{name}.{key}"),
            )
        return None

    async def _variable_validator(
        self,
        *,
        name: str,
        key_path: str | None,
        loc: tuple[str | int, ...],
        environment: str,
    ) -> None:
        async with VariablesService.with_session() as service:
            defined_variables = await service.search_variables(
                VariableSearch(names={name}, environment=environment)
            )

        if (n_found := len(defined_variables)) != 1:
            self.add(
                status="error",
                msg=(
                    f"Found {n_found} variables matching {name!r} in the {environment!r} environment."
                ),
                type=ExprType.VARIABLE,
                loc=loc,
            )
            return

        values = defined_variables[0].values or {}
        if key_path is None:
            self.add(status="success", type=ExprType.VARIABLE)
            return

        segments = key_path.split(".")
        if len(segments) > MAX_VARS_PATH_DEPTH:
            full_path = ".".join([name, *segments])
            self.add(
                status="error",
                msg=(
                    "VARS expressions currently support at most one key segment "
                    f"(`VARS.<name>.<key>`). Got {full_path!r} with {len(segments)} key "
                    "segments after the variable name."
                ),
                type=ExprType.VARIABLE,
                loc=loc,
            )
            return

        current: Any = values
        traversed: list[str] = []

        for segment in segments:
            if not isinstance(current, Mapping):
                parent_path = ".".join([name, *traversed]) if traversed else name
                full_path = ".".join([name, *traversed, segment])
                self.add(
                    status="error",
                    msg=(
                        f"Variable {name!r} has non-object value at {parent_path!r}; "
                        f"cannot access nested key path {full_path!r}"
                    ),
                    type=ExprType.VARIABLE,
                    loc=loc,
                )
                return

            if segment not in current:
                missing_path = ".".join([name, *traversed, segment])
                self.add(
                    status="error",
                    msg=f"Variable {name!r} is missing key path: {missing_path!r}",
                    type=ExprType.VARIABLE,
                    loc=loc,
                )
                return

            traversed.append(segment)
            current = current[segment]

        self.add(status="success", type=ExprType.VARIABLE)

    async def _oauth_validator(
        self,
        *,
        provider: str,
        key: str,
        grant_type: OAuthGrantType,
        loc: tuple[str | int, ...],
    ) -> None:
        provider_key = ProviderKey(id=provider, grant_type=grant_type)
        try:
            async with IntegrationService.with_session() as service:
                integration = await service.get_integration(provider_key=provider_key)
        except Exception as exc:
            self.logger.warning(
                "Failed to validate OAuth provider",
                provider_id=provider,
                grant_type=grant_type.value,
                error=str(exc),
            )
            self.add(
                status="error",
                msg=(
                    "Encountered an error while validating OAuth provider"
                    f" {provider!r} for grant type {grant_type.value!r}."
                ),
                type=ExprType.SECRET,
                loc=loc,
            )
            return

        if integration is None:
            token_expr = f"{ExprContext.SECRETS.value}.{provider}.{key}"
            self.add(
                status="error",
                msg=(
                    f"OAuth provider {provider!r} is not configured for grant type"
                    f" {grant_type.value!r} required by `{token_expr}`"
                ),
                type=ExprType.SECRET,
                loc=loc,
            )
            return

        self.add(status="success", type=ExprType.SECRET)

    @override
    def add(
        self,
        status: Literal["success", "error"],
        msg: str = "",
        type: ExprType = ExprType.GENERIC,
        ref: str | None = None,
        loc: tuple[str | int, ...] | None = None,
        expression: str | None = None,
    ) -> None:
        if status == "success" and not self._keep_success:
            return
        self._validation_details.append(ValidationDetail(loc=loc, msg=msg, type=type))

    @override
    def results(self) -> list[ValidationDetail]:
        """Return all validation results."""
        return self._validation_details

    @override
    def errors(self) -> list[ValidationDetail]:
        """Return all validation errors."""
        return self._validation_details

    # Nodes

    def actions(self, node: Tree[Token]):  # ty: ignore[invalid-method-override]
        token = node.children[0]
        self.logger.trace("Visit action expression", node=node, child=token)
        if not isinstance(token, Token):
            raise ValueError("Expected a string token")
        jsonpath = token.lstrip(".")
        # ACTIONS.<ref>.<prop> [INDEX] [ATTRIBUTE ACCESS]
        ref, prop, *_ = jsonpath.split(".")
        if ref not in self._context.action_refs:
            self.add(
                status="error",
                msg=f"Invalid action reference {ref!r} in `{ExprContext.ACTIONS.value}.{jsonpath}`",
                type=ExprType.ACTION,
                loc=("expression", f"{ExprContext.ACTIONS.value}.{jsonpath}"),
            )
        # Check prop - TaskResult is a Pydantic model, use model_fields
        valid_props_list = list(TaskResult.model_fields.keys())
        valid_properties = "|".join(valid_props_list)
        pattern = rf"({valid_properties})(\[(\d+|\*)\])?"  # e.g. "result[0], result[*], result"
        if not re.match(pattern, prop):
            self.add(
                status="error",
                msg=(
                    f"Invalid attribute {prop!r} follows action reference {ref!r} in `{ExprContext.ACTIONS.value}.{jsonpath}`."
                    f"\nAttributes following the action reference must be one of {', '.join(map(repr, valid_props_list))}."
                    f"\ne.g. `{ref}.{valid_props_list[0]}`"
                ),
                type=ExprType.ACTION,
                loc=("expression", f"{ExprContext.ACTIONS.value}.{jsonpath}"),
            )
        else:
            self.add(status="success", type=ExprType.ACTION)

    def secrets(self, node: Tree[Token]):
        name_key = super().secrets(node)
        logger.trace("Visit secrets expression", name_key=name_key)
        if name_key is None:
            return
        name, key = name_key
        if name.endswith("_oauth"):  # <provider_id>_oauth.<key>
            provider_id = name.removesuffix("_oauth")
            expected_prefix = provider_id.upper()

            def error_msg() -> str:
                return f"OAuth token must be {expected_prefix}_SERVICE_TOKEN or {expected_prefix}_USER_TOKEN"

            if key.endswith("SERVICE_TOKEN"):
                grant_type = OAuthGrantType.CLIENT_CREDENTIALS
                prefix = key.removesuffix("_SERVICE_TOKEN")
            elif key.endswith("USER_TOKEN"):
                grant_type = OAuthGrantType.AUTHORIZATION_CODE
                prefix = key.removesuffix("_USER_TOKEN")
            else:
                self.add(
                    status="error",
                    msg=error_msg(),
                    type=ExprType.SECRET,
                    loc=self._loc,
                )
                return
            # Prefix is the provider_id in uppercase.
            if prefix != expected_prefix:
                self.add(
                    status="error",
                    msg=error_msg(),
                    type=ExprType.SECRET,
                    loc=self._loc,
                )
                return
            coro = self._oauth_validator(
                provider=provider_id, key=key, grant_type=grant_type, loc=self._loc
            )
            self._task_group.create_task(coro)
        else:
            # Check that we've defined the secret in the SM
            coro = self._secret_validator(
                name=name, key=key, environment=self._environment, loc=self._loc
            )
            self._task_group.create_task(coro)

    def vars(self, node: Tree[Token]):
        name_key = super().vars(node)
        self.logger.trace("Visit vars expression", name_key=name_key)
        if name_key is None:
            return
        name, key_path = name_key
        expr = ExprContext.VARS.value + "." + name
        if key_path:
            expr = f"{expr}.{key_path}"
        self._task_group.create_task(
            self._variable_validator(
                name=name,
                key_path=key_path,
                loc=("expression", expr),
                environment=self._environment,
            )
        )

    def trigger(self, node: Tree[Token]):  # ty: ignore[invalid-method-override]
        self.logger.trace("Visit trigger expression", node=node)
        self.add(status="success", type=ExprType.TRIGGER)

    def env(self, node: Tree[Token]):  # ty: ignore[invalid-method-override]
        self.logger.trace("Visit env expression", node=node)
        self.add(status="success", type=ExprType.ENV)

    def local_vars(self, node: Tree[Token]):  # ty: ignore[invalid-method-override]
        self.logger.trace("Visit local vars expression", node=node)
        self.add(status="success", type=ExprType.LOCAL_VARS)

    def iterator(self, node: Tree):
        iter_var_assign_expr, collection, *_ = node.children
        self.logger.trace(
            "Visit iterator expression",
            iter_var_expr=iter_var_assign_expr,
            collection=collection,
        )
        if iter_var_assign_expr.data != "local_vars_assignment":
            self.add(
                status="error",
                msg="Invalid variable assignment in `for_each`."
                " Please use `var.my_variable`",
                type=ExprType.ITERATOR,
            )
        denylist = ("local_vars", "local_vars_assignment")
        if collection.data in denylist:
            self.add(
                status="error",
                msg=f"You cannot use {', '.join(repr(e) for e in denylist)} expressions in the `for_each` collection.",
                type=ExprType.ITERATOR,
            )


class TemplateActionValidationContext(BaseModel):
    """Context for template action expression validation."""

    expects: dict[str, ExpectedField]  # From TemplateActionDefinition
    step_refs: set[str]  # Valid step references


class TemplateActionExprValidator(
    BaseExprValidator[TemplateActionExprValidationResult]
):
    """Validator for template action expressions."""

    _visitor_name = "TemplateActionExprValidator"
    _expr_kind = "Template Actions"

    def __init__(
        self,
        validation_context: TemplateActionValidationContext,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._context = validation_context
        self._results: list[TemplateActionExprValidationResult] = []

    @override
    def results(self) -> Iterator[TemplateActionExprValidationResult]:
        yield from self._results

    @override
    def errors(self) -> list[TemplateActionExprValidationResult]:
        return [res for res in self.results() if res.status == "error"]

    def add(
        self,
        status: Literal["success", "error"],
        msg: str = "",
        type: ExprType = ExprType.GENERIC,
        ref: str | None = None,
        loc: tuple[str | int, ...] | None = None,
    ) -> None:
        self._results.append(
            TemplateActionExprValidationResult(
                status=status,
                msg=msg,
                expression_type=type,
                loc=loc or self._loc,
                ref=ref,
            )
        )

    def template_action_inputs(self, node: Tree[Token]) -> None:
        """Validate template action input references."""
        token = node.children[0]
        if not isinstance(token, Token):
            raise ValueError("Expected a string token")

        jsonpath = token.lstrip(".")
        input_field = jsonpath.split(".")[0]  # Get first segment

        if input_field not in self._context.expects:
            self.add(
                status="error",
                msg=f"Invalid input reference {input_field!r}. Valid inputs are: {list(self._context.expects.keys())}",
                type=ExprType.TEMPLATE_ACTION_INPUT,
            )
        else:
            self.add(status="success", type=ExprType.TEMPLATE_ACTION_INPUT)

    def template_action_steps(self, node: Tree[Token]) -> None:
        """Validate template action step references."""
        token = node.children[0]
        if not isinstance(token, Token):
            raise ValueError("Expected a string token")

        jsonpath = token.lstrip(".")
        step_ref = jsonpath.split(".")[0]  # Get first segment

        if step_ref not in self._context.step_refs:
            self.add(
                status="error",
                msg=f"Invalid step reference {step_ref!r}. Valid steps are: {sorted(self._context.step_refs)}",
                type=ExprType.TEMPLATE_ACTION_STEP,
            )
        else:
            self.add(status="success", type=ExprType.TEMPLATE_ACTION_STEP)
