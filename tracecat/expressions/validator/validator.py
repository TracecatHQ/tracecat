import re
from collections.abc import Iterator
from types import TracebackType
from typing import Any, Literal, Self, TypeVar, override

from lark import Token, Tree
from pydantic import BaseModel, Field

from tracecat.concurrency import GatheringTaskGroup
from tracecat.dsl.models import TaskResult
from tracecat.expressions.common import ExprContext, ExprType, eval_jsonpath
from tracecat.expressions.expectations import ExpectedField
from tracecat.expressions.validator.base import BaseExprValidator
from tracecat.logger import logger
from tracecat.secrets.models import SecretSearch
from tracecat.secrets.service import SecretsService
from tracecat.types.exceptions import TracecatExpressionError
from tracecat.validation.models import (
    TemplateActionExprValidationResult,
    ValidationDetail,
)

T = TypeVar("T")


class ExprValidationContext(BaseModel):
    """Container for the validation context of an expression tree."""

    action_refs: set[str]
    inputs_context: Any = Field(default_factory=dict)
    trigger_context: Any = Field(default_factory=dict)


class ExprValidator(BaseExprValidator):
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

    def actions(self, node: Tree[Token]):
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
        # Check prop
        valid_props_list = list(TaskResult.__annotations__.keys())
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
        logger.warning("secrets", name_key=name_key)
        if name_key is None:
            return
        name, key = name_key
        # Check that we've defined the secret in the SM
        coro = self._secret_validator(
            name=name, key=key, environment=self._environment, loc=self._loc
        )
        self._task_group.create_task(coro)

    def inputs(self, node: Tree[Token]):
        self.logger.trace("Visit input expression", node=node)
        token = node.children[0]
        if not isinstance(token, Token):
            raise ValueError("Expected a string token")
        jsonpath = token.lstrip(".")
        try:
            eval_jsonpath(
                jsonpath,
                self._context.inputs_context,
                context_type=ExprContext.INPUTS,
                strict=self._strict,
            )
            self.add(status="success", type=ExprType.INPUT)
        except TracecatExpressionError as e:
            return self.add(status="error", msg=str(e), type=ExprType.INPUT)

    def trigger(self, node: Tree):
        self.logger.trace("Visit trigger expression", node=node)
        self.add(status="success", type=ExprType.TRIGGER)

    def env(self, node: Tree):
        self.logger.trace("Visit env expression", node=node)
        self.add(status="success", type=ExprType.ENV)

    def local_vars(self, node: Tree):
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
        blacklist = ("local_vars", "local_vars_assignment")
        if collection.data in blacklist:
            self.add(
                status="error",
                msg=f"You cannot use {', '.join(repr(e) for e in blacklist)} expressions in the `for_each` collection.",
                type=ExprType.ITERATOR,
            )


class TemplateActionValidationContext(BaseModel):
    """Context for template action expression validation."""

    expects: dict[str, ExpectedField]  # From TemplateActionDefinition
    step_refs: set[str]  # Valid step references


class TemplateActionExprValidator(BaseExprValidator):
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
        self._results: list[TemplateActionExprValidationResult] = []  # Type override

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
    ) -> None:
        self._results.append(
            TemplateActionExprValidationResult(
                status=status,
                msg=msg,
                expression_type=type,
                loc=self._loc,
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
