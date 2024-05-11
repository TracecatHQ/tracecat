# DSL Sample

This sample shows how to have a workflow interpret/invoke arbitrary steps defined in a DSL. It is similar to the DSL
samples [in TypeScript](https://github.com/temporalio/samples-typescript/tree/main/dsl-interpreter) and
[in Go](https://github.com/temporalio/samples-go/tree/main/dsl).

For this sample, the optional `dsl` dependency group must be included. To include, run:

    poetry install --with dsl

To run, first see [README.md](../README.md) for prerequisites. Then, run the following from this directory to start the
worker:

    poetry run python worker.py

This will start the worker. Then, in another terminal, run the following to execute a workflow of steps defined in
[workflow1.yaml](workflow1.yaml):

    poetry run python starter.py workflow1.yaml

This will run the workflow and show the final variables that the workflow returns. Looking in the worker terminal, each
step executed will be visible.

Similarly we can do the same for the more advanced [workflow2.yaml](workflow2.yaml) file:

    poetry run python starter.py workflow2.yaml

This sample gives a guide of how one can write a workflow to interpret arbitrary steps from a user-provided DSL. Many
DSL models are more advanced and are more specific to conform to business logic needs.
