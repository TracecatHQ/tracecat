# Pydantic Converter Sample

This sample shows how to create a custom Pydantic converter to properly serialize Pydantic models.

For this sample, the optional `pydantic` dependency group must be included. To include, run:

    poetry install --with pydantic

To run, first see [README.md](../README.md) for prerequisites. Then, run the following from this directory to start the
worker:

    poetry run python worker.py

This will start the worker. Then, in another terminal, run the following to execute the workflow:

    poetry run python starter.py

In the worker terminal, the workflow and its activity will log that it received the Pydantic models. In the starter
terminal, the Pydantic models in the workflow result will be logged.

### Notes

This is the preferred way to use Pydantic models with Temporal Python SDK. The converter code is small and meant to
embed into other projects.

This sample also demonstrates use of `datetime` inside of Pydantic models. Due to a known issue with the Temporal
sandbox, this class is seen by Pydantic as `date` instead of `datetime` upon deserialization. This is due to a
[known Python issue](https://github.com/python/cpython/issues/89010) where, when we proxy the `datetime` class in the
sandbox to prevent non-deterministic calls like `now()`, `issubclass` fails for the proxy type causing Pydantic to think
it's a `date` instead. In `worker.py`, we have shown a workaround of disabling restrictions on `datetime` which solves
this issue but no longer protects against workflow developers making non-deterministic calls in that module.
