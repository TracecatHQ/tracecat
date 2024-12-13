"""
This is a simple server that is used to test the stress of the Tracecat API.

Use a reverse tunnel like bore.pub to expose this server for testing.
"""

import json
from pathlib import Path

from fastapi import FastAPI

app = FastAPI()


@app.get("/", include_in_schema=False)
def root() -> dict[str, str]:
    return {"message": "Hello world. I am the Test Stress Server."}


@app.get("/data")
def data():
    with Path(__file__).parent.joinpath("data.json").open() as f:
        x = json.load(f)
        return [x for _ in range(10)]


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8989)
