from langfuse import get_client

from tracecat.contexts import ctx_run


def init_langfuse(model_name: str, model_provider: str) -> str | None:
    """Initialize Langfuse client and return the trace id."""
    # Initialize Langfuse client and update trace
    langfuse_client = get_client()

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
