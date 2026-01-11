from tracecat.dsl.v1.types import ExecutionContext, MaterializedContext
from tracecat.expressions.common import ExprContext


def materialize(context: ExecutionContext) -> MaterializedContext:
    # Suposedly resolve context from object storage here
    return {
        ExprContext.ACTIONS: {
            k: v.model_dump(mode="json") for k, v in context.actions.items()
        },
        ExprContext.TRIGGER: context.trigger.model_dump(mode="json")
        if context.trigger
        else None,
        ExprContext.ENV: context.env,
    }
