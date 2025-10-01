"""Shared constants for chat streaming payloads."""

DATA_KEY = "d"
PAYLOAD_KEY = "payload"
SCHEMA_KEY = "schema"
STREAM_SEQUENCE_KEY = "seq"
STREAM_TIMESTAMP_KEY = "ts"
STREAM_SCHEMA_ID = "tracecat.vercel-ai-stream.v1"

# Legacy constants retained for backward compatibility. These should be
# removed once all code paths have been migrated to the Vercel stream format.
END_TOKEN = "[TURN_END]"
END_TOKEN_VALUE = 1
