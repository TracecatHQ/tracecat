# API REPL Script

An interactive Python REPL for sending authenticated API requests to the Tracecat API service.

## Quick Start

```bash
# Make sure your dev environment is running
just dev

# In another terminal, run the REPL
uv run scripts/api_repl.py
# or
python scripts/api_repl.py
```

## Configuration

The script uses environment variables for configuration:

- `TRACECAT__PUBLIC_API_URL` - API base URL (default: `http://localhost/api`)
- `TRACECAT__TEST_USER_EMAIL` - Test user email (default: `test@tracecat.com`)
- `TRACECAT__TEST_USER_PASSWORD` - Test user password (default: `password1234`)
- `TRACECAT__SERVICE_KEY` - Service key for internal API calls (optional)

## Usage

Once the REPL starts, you have access to:

### Available Variables

- `session` - Authenticated `requests.Session` instance
- `base_url` - API base URL
- `user_info` - Current user information
- `service_key` - Service key (if set)
- `console` - Rich console for pretty printing

### Helper Functions

- `pretty_json(data)` - Pretty print JSON with syntax highlighting
- `help_commands()` - Show help message with examples

### Examples

#### List workflows
```python
resp = session.get(f"{base_url}/workflows")
pretty_json(resp.json())
```

#### Create a workflow
```python
resp = session.post(f"{base_url}/workflows", json={
    "title": "My Test Workflow",
    "description": "Created from REPL"
})
workflow = resp.json()
pretty_json(workflow)
```

#### Get a specific workflow
```python
workflow_id = "wf_..."
resp = session.get(f"{base_url}/workflows/{workflow_id}")
pretty_json(resp.json())
```

#### List secrets
```python
resp = session.get(f"{base_url}/organization/secrets")
pretty_json(resp.json())
```

#### Create a secret
```python
resp = session.post(f"{base_url}/organization/secrets", json={
    "type": "custom",
    "name": "my-api-key",
    "keys": [{"key": "API_KEY", "value": "secret-value"}],
    "description": "Test secret"
})
```

#### List registry repositories
```python
resp = session.get(f"{base_url}/registry/repos")
pretty_json(resp.json())
```

#### Make authenticated service calls
```python
from tracecat.types.auth import system_role

role = system_role()
resp = session.post(
    "http://localhost:8001/api/executor/run/core.transform.reshape",
    headers={
        "x-tracecat-service-key": service_key,
        **role.to_headers()
    },
    json={...}
)
```

## Tips

1. **Tab completion**: Use tab for autocompletion of variable names
2. **History**: Use up/down arrows to navigate command history
3. **Multi-line**: Use `\` at end of line for multi-line statements
4. **Help**: Type `help_commands()` for quick reference
5. **Exit**: Press `Ctrl+D` or type `exit()` to quit

## Troubleshooting

### Authentication fails
- Make sure the dev environment is running (`just dev`)
- Check that `TRACECAT__PUBLIC_API_URL` is correct
- Verify credentials with `TRACECAT__TEST_USER_EMAIL` and `TRACECAT__TEST_USER_PASSWORD`

### Connection refused
- Ensure the API service is running on the expected port
- Check `docker-compose ps` to see if containers are healthy

### Service key not found
- The `TRACECAT__SERVICE_KEY` must be set in your environment
- This is required for internal service-to-service API calls
- Check your `.env` file or docker-compose configuration
