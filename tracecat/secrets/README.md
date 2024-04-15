# Secrets management

## Proposed Design

Move from having flat plain value secrets to a more structured approach with key-value pairs. This sets the groundwork for more advanced features like constraining secret keys to specific values, and more.

The proposed design follows Modal's approach to secrets management. The user can create secrets in the secrets manager, and then reference these secrets by name in their workflows.

## Proposed API

This is what the changes will look like from the user's perspective.

> Assuming `my_datadog_secret` and `my_github_secret` are secrets in the secrets manager.
> `my_datadog_secret` contains the key `DATADOG_API_KEY` > `my_github_secret` contains the key `GH_ACCESS_TOKEN`, and `GH_USERNAME`

In the UI:

```json
// A HTTP node:
{
  "headers": {
    "Authorization": "Bearer {{ SECRETS.my_datadog_secret.DATADOG_API_KEY }}"
  }
}
```

For integrations:

```python


from tracecat.integrations.registry import registry

@registry.register(
    description="A simple example integration that uses secrets.",
    secrets=["my_datadog_secret", "my_github_secret"]
)
def my_integration(*args, **kwargs):
    my_datadog_secret = os.environ["DATADOG_API_KEY"]
    my_github_user = os.environ["GH_USERNAME"]
    my_github_secret = os.environ["GH_ACCESS_TOKEN"]
    # Your function body...
```

## Use cases

### Templated secret expressions

The current implementation of this is a flat hierarchy of secrets, invoked in any input field as such: `{{ SECRETS.<secret_name> }}`.

### Custom functions: Integration/code execution/data transform nodes.

- When functions are registered you can specify a list of required secrets.
- These secrets are used to validate that the user has already created these secrets. - These secrets should be then fetched from the secrets manager and passed to the integration function during execution.

The gold standard is a Modal-like interface where you can write regular python code (with calls to os.environ) and the system will resolve the secrets normally.

By design, we already achieve the above separation as we use a `ProcessPoolExecutor` to run custom functions. This means that inside of each child process, we can freely modify the environment without affecting the parent process.

Registering secrets in the decorator does the following:

- Validates that the user has already created these secrets.
- Fetches the secrets from the secrets manager before the function executes. We might possibly need to somehow isolate each workflow's environment secrets.
- Passes the secrets to the integration function during execution.

The decorator should also wrap the function in another function that during runtime will perform the API call to get the secrets from the secrets manager, and load them into the environment.

##
