# Contributing to Tracecat

Tracecat is currently accepting contributions for:
- **Action Templates** and **Python integrations** in [Tracecat Registry](https://docs.tracecat.com/integrations/overview)
- **Inline functions** for Tracecat's [expressions](https://docs.tracecat.com/expressions) engine

**Tracecat Registry** is a collection of integration and response-as-code templates. Response actions, called **Action Templates**, are organized into [MITRE D3FEND](https://d3fend.mitre.org/) categories (detect, isolate, evict, restore, harden, model) and Tracecat's own ontology of capabilities (e.g. `list_alerts`, `list_cases`, `list_users`).
Template inputs (e.g. `start_time`, `end_time`) are normalized to fit the [Open Cyber Security Schema (OCSF)](https://schema.ocsf.io/) ontology where possible.

We have a growing number of HTTP and Python client (e.g. `boto3`, `falconpy`) based integrations contributed by an active open source community.
Come join Tracecat's community of security and IT engineers :heart:, gain development experience with a well-maintained project, and improve your work portfolio through open source!

You'll find us in the `#contributors` channel on [Discord](https://discord.gg/H4XZwsYzY4).

## Git workflow

Please create a [issue](https://github.com/TracecatHQ/tracecat/issues) to collect feedback prior to opening up a [pull request (PR)](https://github.com/TracecatHQ/tracecat/pulls). If possible try to keep PRs scoped to one feature, and add tests for new features. We use the fork-based contribution model described by [GitHub's documentation](https://docs.github.com/en/get-started/exploring-projects-on-github/contributing-to-a-project).
(In short: Fork the Tracecat repo and open a PR back from your fork into our `main` branch.)

We use the following branch naming convention: `{feat/fix}/{short-description}` e.g. `feat/jamf-lock-device-template`.

## Before you start

> [!TIP]
> This is the same development setup used by the core Tracecat team!
> We use [`uv`](https://docs.astral.sh/uv/)'s `pip` [interface](https://docs.astral.sh/uv/pip/) to run tests in an isolated Python environment.

> [!NOTE]
> You can find `tracecat` and `tracecat_registry` Python dependencies under the `[project]` and `[project.optional-dependencies]` sections in the [`pyproject.toml`](https://github.com/TracecatHQ/tracecat/blob/main/pyproject.toml) and [`registry/pyproject.toml`](https://github.com/TracecatHQ/tracecat/blob/main/registry/pyproject.toml) files.

The Tracecat development environment consists of a:

- Tracecat git repository
- Local Docker Compose development stack
- `uv` Python 3.12 [virtual environment](https://docs.astral.sh/uv/pip/environments/)
- `tracecat` and `tracecat_registry` Python packages (as [editable packages](https://setuptools.pypa.io/en/latest/userguide/development_mode.html))
- [`pre-commit`](https://pre-commit.com/) git hook manager
- [`pnpm`](https://pnpm.io/) JavaScript package manager

To set this up, run the following commands:

```bash
# Install uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# Clone repository
git clone https://github.com/TracecatHQ/tracecat.git
cd tracecat

# Create Python 3.12 virtual environment
uv venv --python 3.12

# Install Tracecat and Tracecat Registry
# Python packages and dependencies into venv
uv pip install -e ".[dev]"
uv pip install -e "tracecat_registry[cli] @ ./registry"

# Install pre-commit hooks
uv pip install pre-commit
uv run pre-commit install

# Install frontend dependencies
pnpm install --dir frontend
```

You've now configured a development environment.
Let's now spin up the Tracecat development stack with the following command:

```bash
docker compose --file docker-compose.dev.yml up -d
```

You can now access the Tracecat UI at [http://localhost](http://localhost).
Updates to Python code in `tracecat/` and `registry/` are automatically reflected in the development stack.

> [!IMPORTANT]
> If you update `pyproject.toml` or `registry/pyproject.toml`, with new dependencies, you need to rebuild the Tracecat development stack:
>
> ```bash
> docker compose --file docker-compose.dev.yml down
> docker compose --file docker-compose.dev.yml build --no-cache
> docker compose --file docker-compose.dev.yml up -d
> ```

## Run `pytest`

We use [`pytest`](https://docs.pytest.org/en/stable/) for all Python tests.

```bash
uv run pytest tests/unit  # Run backend (includes inline functions) tests
uv run pytest tests/registry  # Run registry (core actions, integrations) tests
```

## How to contribute?

We currently support contributions for new integrations and inline functions.

### YAML Action Templates

> [!NOTE]
> You can find existing Action Templates in the [`registry/tracecat_registry/templates/`](https://github.com/TracecatHQ/tracecat/tree/main/registry/tracecat_registry/templates) directory.

Every **Action Template** must be a YAML file that:

- Follows Tracecat's template [schema](https://docs.tracecat.com/integrations/action-templates).
- Has `expects` with arguments that match a supported [**Response Schema**](https://github.com/TracecatHQ/tracecat/tree/main/registry/tracecat_registry/schemas) (e.g. `list_alerts`, `list_cases`, `list_users`).
- All required arguments in the API call in `steps` are mapped to an argument in a **Response Schema**.
- No optional arguments in the API call in `steps` are specified, unless required by a **Response Schema** or satisfies one of the conditions below.

**Optional arguments**


**`base_url`**
If the integration has a paramterized API URL (e.g. Jira, Microsoft Graph), add the following argument to the `expects` section:

```yaml
expects:
  base_url:
    type: str
    description: Base URL for the API
```

**Example template**

```yaml
type: action
definition:
title: Lock device
description: Lock a device managed by Jamf Pro with a user-provided 6-digit pin.
display_group: Jamf
doc_url: https://developer.jamf.com/jamf-pro/reference/post_v2-mdm-commands
namespace: tools.jamf
name: lock_device
expects:
  device_id:
    type: str
    description: Management ID of the device to lock.
  message:
    type: str
    description: Message to display on the device.
  pin:
    type: str
    description: 6-digit PIN to lock and unlock the device.
  base_url:
    type: str
    description: Base URL for the Jamf Pro API.
steps:
  - ref: get_access_token
    action: tools.jamf.get_access_token
    args:
      base_url: ${{ inputs.base_url }}
  - ref: post_mdm_command
    action: core.http_request
    args:
      url: ${{ inputs.base_url }}/api/v2/mdm/commands
      method: POST
      headers:
        Authorization: Bearer ${{ steps.get_access_token.result }}
      payload:
        clientData:
          managementId: ${{ inputs.device_id }}
        commandData:
          commandType: DEVICE_LOCK
          message: ${{ inputs.message }}
          pin: ${{ inputs.pin }}
returns: ${{ steps.post_mdm_command.result }}
```

### Response Schemas

> [!NOTE]
> You can find existing YAML schemas in the [`registry/tracecat_registry/schemas/`](https://github.com/TracecatHQ/tracecat/tree/main/registry/tracecat_registry/schemas) directory.

If you can't find a schema that matches your integration or want to suggest a change to an existing schema, please [open an issue](https://github.com/TracecatHQ/tracecat/issues). Provide the following information:

- Link to the closest [MITRE D3FEND](https://d3fend.mitre.org/) category (e.g. `detect`, `isolate`, `evict`, `restore`, `harden`, `model`).
- Links to the relevant [OCSF](https://schema.ocsf.io/) classes and objects.
- Links to any similar or related schemas in Tracecat Registry.
- (If applicable) Suggested name of the new capability (e.g. `list_alerts`, `list_cases`, `list_users`)

### Python Integrations

> [!NOTE
> You can find existing Python integrations in the [`registry/tracecat_registry/integrations/`](https://github.com/TracecatHQ/tracecat/tree/main/registry/tracecat_registry/integrations) directory.

### Inline Functions

> [!NOTE]
> You can find existing inline functions in the [`tracecat/expressions/functions.py`](https://github.com/TracecatHQ/tracecat/blob/main/tracecat/expressions/functions.py) file.

## Sharing Ideas / Feature Requests

If you have an idea or feature request, please [open an issue](https://github.com/TracecatHQ/tracecat/issues) or join our [Discord server](https://discord.gg/H4XZwsYzY4) to discuss in the `#questions` channel!

## Reporting Bugs

If you encounter an invalid Action Template, Python integration, or inline function, please [open an issue](https://github.com/TracecatHQ/tracecat/issues).
Make sure to provide the following information:

- Tracecat version (e.g. `0.26.1`)

## Frontend / Backend Contributions

We do not officially support contributions to `api`, `worker`, `executor`, or `ui` services.

Nevertheless, if you're an experienced Python or JavaScript developer with a well-scoped and urgent contribution,
please contact the Tracecat team on [Discord](https://discord.gg/H4XZwsYzY4) for guidance.
