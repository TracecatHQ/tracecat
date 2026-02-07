# Stress Test Webhook

Send large JSON payloads to a webhook endpoint to verify that result externalization to MinIO is working correctly. This tests that payloads exceeding Temporal's ~2MB blob limit are properly externalized to object storage.

## Arguments

The webhook URL is required: `$ARGUMENTS`

If no URL is provided, ask the user for the webhook URL before proceeding.

## Steps

1. **Verify the webhook URL**
   - Confirm `$ARGUMENTS` contains a valid webhook URL (should contain `/webhooks/`)
   - Extract the base URL to determine the API host

2. **Check externalization is enabled**
   - Shell into the running API container and verify the environment variables:
   ```bash
   just cluster attach api -- env | grep -E "TRACECAT__RESULT_EXTERNALIZATION_ENABLED|TRACECAT__RESULT_EXTERNALIZATION_SIZE_LIMIT"
   ```
   - `TRACECAT__RESULT_EXTERNALIZATION_ENABLED` must be `true`
   - If externalization is disabled, warn the user and ask whether to proceed (payloads > ~2MB will fail with Temporal blob size errors)

3. **Generate and send test payloads**
   - For each size in **1MB, 2MB, 3MB, 4MB, 5MB, 10MB, 20MB**, generate a JSON payload and POST it to the webhook URL
   - Use Python to generate payloads and curl to send them:
   ```bash
   python3 -c "
   import json, sys
   target_bytes = int(sys.argv[1]) * 1024 * 1024
   # Generate array of objects to reach target size
   item = {'id': 'x' * 100, 'data': 'y' * 900}
   item_size = len(json.dumps(item))
   count = target_bytes // item_size
   payload = json.dumps({'items': [item] * count})
   sys.stdout.write(payload)
   " SIZE_IN_MB > /tmp/claude/stress_payload.json

   curl -s -o /tmp/claude/stress_response.txt -w "%{http_code} %{time_total}" \
     -X POST "$WEBHOOK_URL" \
     -H "Content-Type: application/json" \
     -d @/tmp/claude/stress_payload.json
   ```
   - Record the HTTP status code and response time for each request
   - Wait 2 seconds between requests to avoid overwhelming the system

4. **Report results as a summary table**
   - Format:
   ```
   | Size | HTTP Status | Response Time | Response Snippet |
   |------|-------------|---------------|------------------|
   | 1MB  | 200         | 0.089s        | {"status":"ok"...}|
   ```
   - Flag any non-200 responses as failures

5. **Check worker/executor logs for errors**
   - After all requests complete, wait 10 seconds for workflows to process
   - Check the worker and executor logs for errors:
   ```bash
   just cluster logs --tail 100 worker 2>&1 | grep -i -E "error|exception|blob.*limit|failed"
   just cluster logs --tail 100 executor 2>&1 | grep -i -E "error|exception|failed"
   ```
   - Report any errors found

6. **Verify workflow executions completed**
   - Check worker logs to confirm workflows completed successfully:
   ```bash
   just cluster logs --tail 200 worker 2>&1 | grep -i "workflow completed"
   ```
   - Report how many of the expected workflows completed

## Important Notes

- Externalization must be enabled for payloads > ~2MB to work. Without it, the full payload is sent inline to Temporal which has a ~2MB blob size limit.
- With externalization enabled, payloads > the configured size limit (default 128KB) are stored in MinIO/S3 and only a small reference (~6KB) flows through Temporal.
- Clean up generated payload files after the test completes.
- If any requests fail, check `just cluster logs api` for the API-side errors as well.
