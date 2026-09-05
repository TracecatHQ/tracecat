"""The replay endpoint publishes its handled errors to API clients."""

from fastapi import FastAPI

from tracecat.workflow.executions.router import router


def test_run_from_action_declares_handled_errors() -> None:
    app = FastAPI()
    app.include_router(router)
    operations = app.openapi()["paths"]
    path = next(path for path in operations if path.endswith("/draft/from-action"))
    responses = operations[path]["post"]["responses"]
    assert {"200", "400", "404", "422"} <= responses.keys()
