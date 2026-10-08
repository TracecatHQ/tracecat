# Service Authorization Reference

## Endpoint

**URL pattern:** `https://servicereference.us-east-1.amazonaws.com/v1/<service>/<service>.json`

These files are large (tens to hundreds of KB). Always extract only what you need.

## Query Patterns

Use the `service_reference_query` tool when available. If unavailable, use `curl` piped to `jq`.

### Pattern 1: Authorized actions for an operation (most common)

```json
{ "service": "s3", "operation": "CopyObject" }
```

Returns the actions needed to authorize the operation, including cross-service actions.

### Pattern 2: Verify an action name exists

```json
{ "service": "s3", "action": "GetObject" }
```

Use when building conditions or when an operation has no `Operations` entry.

### Pattern 3: Look up a resource ARN format

```json
{ "service": "s3", "resource": "bucket" }
```

### Pattern 4: Check a condition key's type

```json
{ "service": "s3", "condition_key": "aws:TagKeys" }
```

Essential before using `ForAnyValue`/`ForAllValues` — these operators MUST only be used with array-typed keys (`ArrayOfString`, `ArrayOfARN`, etc.).

### Pattern 5: List all operations or actions for a service

```json
{ "service": "dynamodb", "list": "operations" }
```

If the operation name is not found, the tool returns the list of available operations.

## Reference Structure

Each service reference JSON contains four top-level arrays:

- **Actions** — IAM actions with resource types and condition keys
- **Operations** — API operations mapped to authorized actions (available for most services; absent for a few)
- **Resources** — Resource type definitions with ARN formats
- **ConditionKeys** — Condition key definitions with types (String, ArrayOfString, Bool, etc.)

Each Operation entry contains:

- **Name** — The API operation name (e.g., `CreateFunction`)
- **AuthorizedActions** — IAM actions required, each with `Name`, `Service` (may differ from the queried service for cross-service actions), and optional `Context`

## CLI Fallback

When the `service_reference_query` tool is unavailable, use `curl` and `jq`:

```bash
# Get authorized actions for an operation
curl -s "https://servicereference.us-east-1.amazonaws.com/v1/lambda/lambda.json" | \
  jq '.Operations[] | select(.Name == "CreateFunction")'

# Verify an action exists
curl -s "https://servicereference.us-east-1.amazonaws.com/v1/s3/s3.json" | \
  jq '.Actions[] | select(.Name == "GetObject")'

# Look up resource ARN format
curl -s "https://servicereference.us-east-1.amazonaws.com/v1/s3/s3.json" | \
  jq '.Resources[] | select(.Name == "bucket")'

# Check condition key type
curl -s "https://servicereference.us-east-1.amazonaws.com/v1/s3/s3.json" | \
  jq '.ConditionKeys[] | select(.Name == "aws:TagKeys")'
```
