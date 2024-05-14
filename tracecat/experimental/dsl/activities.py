from __future__ import annotations

from typing import Any

from pydantic import BaseModel
from temporalio import activity

from tracecat.experimental.actions.example import my_function


class ActionInputs(BaseModel):
    pass


class WebhookAction(ActionInputs):
    inputs: dict[str, Any]


class HTTPRequestAction(ActionInputs):
    url: str


class PrintAction(ActionInputs):
    value: int


class UDFAction(ActionInputs):
    inputs: dict[str, Any]


class DSLActivities:
    # We likely want to get these validators dynamically
    # from the function definition of each activity
    validators: dict[str, ActionInputs] = {
        "run_webhook_action": WebhookAction,
        "run_http_request_action": HTTPRequestAction,
        "run_udf": UDFAction,
        "run_print_action": PrintAction,
    }

    def __new__(cls):
        raise RuntimeError("This class should not be instantiated")

    @staticmethod
    @activity.defn
    async def run_webhook_action(args: WebhookAction) -> dict[str, Any]:
        activity.logger.info("Webhook action")
        print(args.inputs)
        return args.inputs

    @staticmethod
    @activity.defn
    async def run_http_request_action(args: HTTPRequestAction) -> str:
        activity.logger.info("Run HTTP request action")
        result = f"Result from hitting {args.url}"
        return result

    @staticmethod
    @activity.defn
    async def run_udf(args: UDFAction) -> Any:
        activity.logger.info("Run udf")
        print(args)
        result = my_function()
        activity.logger.info(f"Result: {result}")
        return result

    @staticmethod
    @activity.defn
    async def run_print_action(args: PrintAction) -> str:
        activity.logger.info("Run print action")
        print(type(args.value), args.value)
        return args.value


# Dynamically register all static methods as activities
dsl_activities = [
    getattr(DSLActivities, method_name)
    for method_name in dir(DSLActivities)
    if hasattr(getattr(DSLActivities, method_name), "__temporal_activity_definition")
]
