from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, TypeVar, cast

import yaml
from pydantic import BaseModel, Field, computed_field, model_validator
from tracecat_registry import RegistrySecret

from tracecat.dsl.models import DSLNodeResult
from tracecat.expressions.eval import eval_templated_object
from tracecat.expressions.expectations import ExpectedField
from tracecat.logger import logger

if TYPE_CHECKING:
    from tracecat.registry.store import Registry

ArgsT = TypeVar("ArgsT", bound=Mapping[str, Any])


class ActionLayer(BaseModel):
    ref: str = Field(..., description="The reference of the layer")
    action: str
    args: ArgsT

    async def run(self, *, registry: Registry, context: dict[str, Any]) -> Any:
        udf = registry.get(self.action)
        udf.validate_args(**self.args)
        concrete_args = cast(ArgsT, eval_templated_object(self.args, operand=context))
        return await udf.run_async(args=concrete_args, context=context)


class TemplateActionDefinition(BaseModel):
    name: str = Field(..., description="The action name")
    namespace: str = Field(..., description="The namespace of the action")
    title: str = Field(..., description="The title of the action")
    description: str = Field("", description="The description of the action")
    display_group: str = Field(..., description="The display group of the action")
    secrets: list[RegistrySecret] | None = Field(
        None, description="The secrets to pass to the action"
    )
    expects: dict[str, ExpectedField] = Field(
        ..., description="The arguments to pass to the action"
    )
    layers: list[ActionLayer] = Field(
        ..., description="The internal layers of the action"
    )
    returns: str | list[str] | dict[str, Any] = Field(
        ..., description="The result of the action"
    )

    # Validate layers
    @model_validator(mode="after")
    def validate_layers(self) -> TemplateActionDefinition:
        layer_refs = [layer.ref for layer in self.layers]
        unique_layer_refs = set(layer_refs)

        if len(layer_refs) != len(unique_layer_refs):
            duplicate_layer_refs = [
                ref for ref in layer_refs if layer_refs.count(ref) > 1
            ]
            raise ValueError(
                f"Duplicate layer references found: {duplicate_layer_refs}"
            )

        return self

    @computed_field
    @property
    def action(self) -> str:
        return f"{self.namespace}.{self.name}"


class TemplateAction(BaseModel):
    type: Literal["action"] = Field("action", frozen=True)
    definition: TemplateActionDefinition

    @staticmethod
    def from_yaml(path: Path) -> TemplateAction:
        with path.open("r") as f:
            template = yaml.safe_load(f)

        return TemplateAction(**template)

    async def run(
        self,
        *,
        args: dict[str, Any],
        base_context: dict[str, Any],
        registry: Registry,
    ) -> dict[str, Any]:
        """Run the layers of the action.

        Assumptions:
        - Args have been validated against the expected arguments
        - All expressions are deterministic

        Returns
        -------
        - If no return is specified, we return the last layer's result
        - If a return is specified, we return the result of the expression
        """

        context = base_context.copy() | {"inputs": args, "layers": {}}
        logger.info("Running template action", action=self.definition.action)
        for layer in self.definition.layers:
            result = await layer.run(context=context, registry=registry)
            context["layers"][layer.ref] = DSLNodeResult(
                result=result,
                result_typename=type(result).__name__,
            )

        # Handle returns
        return eval_templated_object(self.definition.returns, operand=context)
