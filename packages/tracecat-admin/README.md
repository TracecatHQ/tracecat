# Tracecat Admin CLI

CLI tool for Tracecat platform operators to manage infrastructure and control plane operations.

## Installation

```bash
# Install the CLI
pip install tracecat-admin

# For bootstrap operations (create-superuser with direct DB access)
pip install tracecat-admin[bootstrap]
```

## Configuration

The CLI uses environment variables for configuration:

| Variable | Description | Required |
|----------|-------------|----------|
| `TRACECAT__API_URL` | Tracecat API URL (default: `http://localhost:8000`) | No |
| `TRACECAT__SERVICE_KEY` | Service key for API authentication | Yes (for API commands) |
| `TRACECAT__DB_URI` | Database URI for direct DB operations | Yes (for bootstrap/migrate) |

## Commands

### Admin Commands

```bash
# List all users
tracecat admin list-users

# Get user details
tracecat admin get-user <user-id>

# Promote a user to superuser (via API)
tracecat admin promote-user --email user@example.com

# Demote a user from superuser (via API)
tracecat admin demote-user --email user@example.com

# Create a superuser (direct DB - for bootstrap)
tracecat admin create-superuser --email admin@example.com

# Create a new user and promote to superuser
tracecat admin create-superuser --email admin@example.com --create
```

### Organization Commands

```bash
# List all organizations
tracecat orgs list

# Create a new organization
tracecat orgs create --name "My Org" --slug "my-org"

# Get organization details
tracecat orgs get <org-id>
```

### Registry Commands

```bash
# Sync all registry repositories
tracecat registry sync

# Sync a specific repository
tracecat registry sync --repository-id <repo-id>

# Get registry status
tracecat registry status

# List registry versions
tracecat registry versions
```

### Migration Commands

```bash
# Upgrade database to latest
tracecat migrate upgrade head

# Upgrade to specific revision
tracecat migrate upgrade <revision>

# Downgrade database
tracecat migrate downgrade -1

# Show current migration status
tracecat migrate status

# Show migration history
tracecat migrate history
```

## Output Formats

All commands support JSON output with the `--json` or `-j` flag:

```bash
tracecat admin list-users --json
tracecat orgs list -j
```

## Development

```bash
# Install in development mode
pip install -e packages/tracecat-admin

# Run the CLI
tracecat --help
```
