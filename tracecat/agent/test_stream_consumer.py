# Write a fastapi server that receives post requests from /stream
# Usage: uv run tests/server.py
import json

from fastapi import FastAPI
from pydantic import BaseModel, TypeAdapter
from pydantic_ai import messages

app = FastAPI()

# Create TypeAdapter for AgentStreamEvent
ta_stream_event = TypeAdapter(messages.AgentStreamEvent)


class StreamRequest(BaseModel):
    event: str  # JSON string of AgentStreamEvent


@app.post("/stream")
async def stream(request: StreamRequest) -> dict[str, str]:
    try:
        # Parse the JSON string to dict first
        event_dict = json.loads(request.event)
        # Then validate with TypeAdapter
        event = ta_stream_event.validate_python(event_dict)

        if isinstance(event, messages.PartStartEvent):
            if isinstance(event.part, messages.ToolCallPart):
                print(f"\n🔧 [Tool Call: {event.part.tool_name}]", flush=True)
            elif isinstance(event.part, messages.TextPart):
                print("\n💬 ", end="", flush=True)

        elif isinstance(event, messages.PartDeltaEvent):
            if isinstance(event.delta, messages.TextPartDelta):
                print(event.delta.content_delta, end="", flush=True)
            elif isinstance(event.delta, messages.ToolCallPartDelta):
                if event.delta.args_delta:
                    # Only show meaningful args deltas, not fragments
                    if len(event.delta.args_delta) > 5:
                        print(".", end="", flush=True)

        elif isinstance(event, messages.FunctionToolCallEvent):
            tool_name = event.part.tool_name
            try:
                if isinstance(event.part.args, str):
                    args = json.loads(event.part.args) if event.part.args else {}
                else:
                    args = event.part.args or {}
                args_str = json.dumps(args, indent=0).replace("\n", " ")
            except json.JSONDecodeError:
                args_str = str(event.part.args)
            print(f"\n   → Calling: {tool_name}({args_str})", flush=True)

        elif isinstance(event, messages.FunctionToolResultEvent):
            tool_name = event.result.tool_name
            content = event.result.content
            if isinstance(content, str):
                result_str = content[:100] + "..." if len(content) > 100 else content
            else:
                result_str = str(content)
            print(f"\n   ✓ Result: {result_str}", flush=True)

        elif isinstance(event, messages.FinalResultEvent):
            print("\n\n✅ [Stream Completed]", flush=True)
            print("-" * 50, flush=True)

    except (json.JSONDecodeError, ValueError) as e:
        print(f"❌ Error parsing event: {e}")
        print(f"   Raw event: {request.event[:200]}...")

    return {"message": "Event received"}


if __name__ == "__main__":
    import uvicorn

    # log_level="warning" suppresses the INFO access logs
    uvicorn.run(app, host="0.0.0.0", port=1234, log_level="warning")
