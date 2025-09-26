"""Langfuse observability helpers."""

from tracecat_registry import secrets

from tracecat.contexts import ctx_run
from tracecat.logger import logger

try:
    from langfuse import get_client
except ImportError:
    get_client = None


def init_langfuse(model_name: str | None, model_provider: str | None) -> str | None:
    """Initialize Langfuse client and return the trace id when Langfuse is available."""

    if get_client is None or secrets.get_or_default("LANGFUSE_PUBLIC_KEY") is None:
        logger.info("Langfuse client not available; skipping trace initialization")
        return None

    langfuse_client = get_client(
        public_key=secrets.get_or_default("LANGFUSE_PUBLIC_KEY")
    )
    logger.info("Found Langfuse credentials; initialized Langfuse client.")

    # Get workflow context for session_id
    run_context = ctx_run.get()
    if run_context:
        session_id = f"{run_context.wf_id}/{run_context.wf_run_id}"
        tags = ["action:ai.agent"]
        if model_name:
            tags.append(model_name)
        if model_provider:
            tags.append(model_provider)

        langfuse_client.update_current_trace(
            session_id=session_id,
            tags=tags,
        )

    # Get the current trace_id
    trace_id = langfuse_client.get_current_trace_id()
    return trace_id
