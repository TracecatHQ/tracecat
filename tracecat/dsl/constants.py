DEFAULT_ACTION_TIMEOUT = 300  # Seconds
MAX_DO_WHILE_ITERATIONS = 2048

# Safety cap for `retry_until` loops. Semantically retry_until is a do-while
# (it always executes at least once), so it reuses the do-while cap value
# under a distinct name to keep the intent explicit. Without this cap, a
# retry_until condition that never evaluates truthy runs unbounded until
# Temporal's history limits reject the execution with an opaque error.
MAX_RETRY_UNTIL_ITERATIONS = MAX_DO_WHILE_ITERATIONS
