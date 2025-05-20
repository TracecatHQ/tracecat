import re
from collections.abc import Awaitable, Iterator
from itertools import chain
from typing import Any, Literal, TypeVar, override

import jsonpath_ng.exceptions
import jsonpath_ng.ext
from lark import Token, Tree, Visitor, v_args
from lark.exceptions import VisitError
from pydantic import BaseModel, Field

from tracecat.concurrency import GatheringTaskGroup
from tracecat.dsl.models import TaskResult
from tracecat.expressions import functions
from tracecat.expressions.common import (
    VISITOR_NODE_TO_EXPR_TYPE,
    ExprContext,
    ExprType,
    eval_jsonpath,
)
from tracecat.expressions.expectations import ExpectedField
from tracecat.logger import logger
from tracecat.secrets.constants import DEFAULT_SECRETS_ENVIRONMENT
from tracecat.types.exceptions import TracecatExpressionError
from tracecat.validation.models import (
    ExprValidationResult,
    TemplateActionExprValidationResult,
    ValidationDetail,
)

T = TypeVar("T")


class ExprValidationContext(BaseModel):
    """Container for the validation context of an expression tree."""

    action_refs: set[str]
    inputs_context: Any = Field(default_factory=dict)
    trigger_context: Any = Field(default_factory=dict)


class BaseExprValidator(Visitor):
    """Base validator containing common validation logic.

    You should not use this class directly, but rather use one of the subclasses.
    """

    _visitor_name: str
    _expr_kind: str

    def __init__(
        self,
        validators: dict[ExprType, Awaitable[ExprValidationResult]] | None = None,
        *,
        environment: str = DEFAULT_SECRETS_ENVIRONMENT,
        strict: bool = True,
    ) -> None:
        self._results: list[ExprValidationResult] = []
        self._validation_details: list[ValidationDetail] = []
        self._strict = strict
        self._loc: tuple[str | int, ...] = ("expression",)
        self._environment = environment
        self._validators = validators or {}
        self.logger = logger.bind(visitor=self._visitor_name)

    """Utility"""

    def add(
        self,
        status: Literal["success", "error"],
        msg: str = "",
        type: ExprType = ExprType.GENERIC,
        ref: str | None = None,
        expression: str | None = None,
    ) -> None:
        self._results.append(
            ExprValidationResult(
                status=status,
                msg=msg,
                expression_type=type,
                ref=ref,
                expression=expression,
            )
        )
        self._validation_details.append(
            ValidationDetail(
                loc=("expression", expression) if expression else ("expression",),
                msg=msg,
                type=type,
            )
        )

    def results(self) -> Iterator[ExprValidationResult]:
        """Return all validation results."""
        yield from self._results

    def errors(self) -> list[ExprValidationResult]:
        """Return all validation errors."""
        return [res for res in self.results() if res.status == "error"]

    @property
    def details(self) -> list[ValidationDetail]:
        """Return all validation details."""
        return self._validation_details

    def visit_with_locator(
        self,
        tree: Tree,
        loc: tuple[str | int, ...] | None = None,
        exclude: set[ExprType] | None = None,
    ) -> Any:
        self._loc = loc or self._loc
        self._exclude = exclude or set()
        return self.visit(tree)

    @override
    def visit(self, tree: Tree) -> Any:
        try:
            if VISITOR_NODE_TO_EXPR_TYPE.get(tree.data) in self._exclude:
                logger.trace("Skipping node", node=tree.data)
                return
            return super().visit(tree)
        except VisitError as e:
            self.handle_error(str(e))

    def handle_error(self, expr: str) -> Any:
        self.add(
            status="error",
            msg=f"Invalid expression: {expr!r}",
            type=ExprType.GENERIC,
        )

    """Visitors"""

    def root(self, node: Tree):
        self.logger.trace("Visiting root:", node=node)

    def trailing_typecast_expression(self, node: Tree):
        _, typename = node.children
        self.logger.trace("Visit trailing cast expression", typename=typename)
        if typename not in functions.BUILTIN_TYPE_MAPPING:
            self.add(
                status="error",
                msg=f"Invalid type {typename!r} in trailing cast expression."
                f" Valid types are {list(functions.BUILTIN_TYPE_MAPPING.keys())}",
                type=ExprType.TYPECAST,
            )
        else:
            self.add(status="success", type=ExprType.TYPECAST)

    def actions(self, node: Tree[Token]):
        self.add(
            status="error",
            type=ExprType.ACTION,
            msg=f"ACTIONS expressions are not supported in {self._expr_kind}",
        )

    def inputs(self, node: Tree[Token]):
        self.add(
            status="error",
            type=ExprType.INPUT,
            msg=f"INPUTS expressions are not supported in {self._expr_kind}",
        )

    def trigger(self, node: Tree):
        self.add(
            status="error",
            type=ExprType.TRIGGER,
            msg=f"TRIGGER expressions are not supported in {self._expr_kind}",
        )

    def env(self, node: Tree):
        self.add(
            status="error",
            type=ExprType.ENV,
            msg=f"ENV expressions are not supported in {self._expr_kind}",
        )

    def local_vars(self, node: Tree):
        self.add(
            status="error",
            type=ExprType.LOCAL_VARS,
            msg=f"var expressions are not supported in {self._expr_kind}",
        )

    def iterator(self, node: Tree):
        self.logger.trace("Visit iterator expression", node=node)
        self.add(
            status="error",
            type=ExprType.ITERATOR,
            msg=f"for_each expressions are not supported in {self._expr_kind}",
        )

    def secrets(self, node: Tree[Token]) -> tuple[str, str] | None:
        self.logger.trace("Visit secret expression", expr=node)

        expr = node.children[0]
        if not isinstance(expr, Token):
            raise ValueError("Expected a string token")
        try:
            sec_path = expr.lstrip(".")
        except ValueError:
            sec_jsonpath = ExprContext.SECRETS + expr
            return self.add(
                status="error",
                msg=f"Invalid secret usage: {sec_jsonpath!r}. Must be in the format `SECRETS.my_secret.KEY`",
                type=ExprType.SECRET,
            )
        parts = sec_path.split(".")
        if len(parts) > 2:
            return self.add(
                status="error",
                msg=f"Invalid secret usage: {sec_path!r}. Got extra segments {parts[2:]!r}."
                "Must be in the format `SECRETS.my_secret.KEY`",
                type=ExprType.SECRET,
            )
        elif len(parts) == 1:
            return self.add(
                status="error",
                msg=f"Invalid secret usage: {sec_path!r}. Must be in the format `SECRETS.my_secret.KEY`",
                type=ExprType.SECRET,
            )
        name, key = parts
        return name, key

    def function(self, node: Tree[Token]):
        fn_name = node.children[0]
        if not isinstance(fn_name, Token):
            raise ValueError("Expected a string token")
        is_mapped = fn_name.endswith(".map")
        fn_name = fn_name.rsplit(".", 1)[0] if is_mapped else fn_name
        self.logger.trace(
            "Visit function expression",
            fn_name=node,
            is_mapped=is_mapped,
            node=node,
        )

        if fn_name not in functions.FUNCTION_MAPPING:
            self.add(
                status="error",
                msg=f"Unknown function name {str(fn_name)!r}",
                type=ExprType.FUNCTION,
            )
        else:
            self.add(status="success", type=ExprType.FUNCTION)

    def ternary(self, node: Tree):
        cond_expr, true_expr, false_expr = node.children
        self.logger.trace(
            "Visit ternary expression",
            cond_expr=cond_expr,
            true_expr=true_expr,
            false_expr=false_expr,
        )
        self.add(status="success", type=ExprType.TERNARY)

    def typecast(self, node: Tree[Token]):
        self.logger.trace("Visit typecast expression")
        typename, *children = node.children
        if typename not in functions.BUILTIN_TYPE_MAPPING:
            return self.add(
                status="error",
                msg=f"Invalid type {typename!r}."
                f" Valid types are {list(functions.BUILTIN_TYPE_MAPPING.keys())}",
                type=ExprType.TYPECAST,
            )

        self.logger.warning("Typecast expression", typename=typename, children=children)
        # If the child is a literal, we can typecast it directly
        child = children[0]
        if not isinstance(child, Tree):
            raise ValueError("Expected a tree")
        if child.data == "literal":
            try:
                functions.cast(child.children[0], typename)
            except ValueError as e:
                self.add(
                    status="error",
                    msg=str(e),
                    type=ExprType.TYPECAST,
                )
        else:
            self.add(status="success", type=ExprType.TYPECAST)

    def literal(self, node: Tree):
        self.logger.trace("Visit literal expression", value=node.children[0])
        self.add(status="success", type=ExprType.LITERAL)

    def jsonpath_expression(self, node: Tree[Token]):
        self.logger.trace("Visiting jsonpath expression", children=node.children)
        try:
            combined_segments = "".join(node.children)  # type: ignore
        except (AttributeError, ValueError) as e:
            self.logger.error("Invalid jsonpath segments", error=str(e))
            self.add(
                status="error",
                msg="Couldn't combine jsonpath expression segments: " + str(e),
            )
        try:
            jsonpath_ng.ext.parse("$" + combined_segments)
        except jsonpath_ng.exceptions.JSONPathError as e:
            self.logger.error("Invalid jsonpath body", error=str(e))
            self.add(
                status="error",
                msg=str(e),
            )

    @v_args(inline=True)
    def jsonpath_segment(self, *args):
        self.logger.trace("Visiting jsonpath segment", args=args)


class ExprValidator(BaseExprValidator):
    """Expression validator for workflow actions."""

    _visitor_name = "ExprValidator"
    _expr_kind = "Workflow Actions"

    def __init__(
        self,
        task_group: GatheringTaskGroup,
        validation_context: ExprValidationContext,
        validators: dict[ExprType, Awaitable[ExprValidationResult]] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(validators, **kwargs)
        self._context = validation_context
        self._task_group = task_group

    def results(self) -> Iterator[ExprValidationResult]:
        """Return all validation results."""
        yield from chain(self._task_group.results(), self._results)

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
                msg=f"Invalid action reference {ref!r} in ACTION expression {jsonpath!r}",
                type=ExprType.ACTION,
            )
        # Check prop
        valid_props_list = list(TaskResult.__annotations__.keys())
        valid_properties = "|".join(valid_props_list)
        pattern = rf"({valid_properties})(\[(\d+|\*)\])?"  # e.g. "result[0], result[*], result"
        if not re.match(pattern, prop):
            self.add(
                status="error",
                msg=(
                    f"Invalid property {prop!r} for action reference {ref!r} in ACTION expression {jsonpath!r}."
                    f"\nUse one of {', '.join(map(repr, valid_props_list))}."
                    f"\ne.g. `{ref}.{valid_props_list[0]}`"
                ),
                type=ExprType.ACTION,
            )
        else:
            self.add(status="success", type=ExprType.ACTION)

    def secrets(self, node: Tree[Token]):
        name_key = super().secrets(node)
        if name_key is None:
            return
        name, key = name_key
        # Check that we've defined the secret in the SM
        coro = self._validators[ExprType.SECRET](
            name=name, key=key, environment=self._environment, loc=self._loc
        )  # type: ignore
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
