"""
This is a simple server that is used to test the stress of the Tracecat API.

Use a reverse tunnel like bore.pub to expose this server for testing.
"""

import json
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException

from tracecat.logger import logger

app = FastAPI()


@app.get("/", include_in_schema=False)
def root() -> dict[str, str]:
    return {"message": "Hello world. I am the Test Stress Server."}


@app.get("/data")
def data():
    with Path(__file__).parent.joinpath("data.json").open() as f:
        x = json.load(f)
        return [x for _ in range(10)]


counter = int(os.getenv("COUNTER", 3))


@app.get("/poll-status")
def simulate_job() -> dict[str, str]:
    """Endpoint that simulates a long-running job by failing a few times before succeeding.

    This endpoint is designed to test HTTP polling functionality:
    - Returns 500 error for first 2 calls
    - Returns success message on 3rd call
    - Resets counter after success
    """
    global counter
    logger.info("Simulating job", counter=counter)
    if counter == 0:
        logger.info("Simulating job success")
        counter = 3
        return {"message": "Hello world. I am the Test Stress Server."}
    else:
        counter -= 1
        logger.info("Simulating job failure")
        raise HTTPException(status_code=404, detail="Not found")


@app.get("/poll-response")
def simulate_job_2() -> dict[str, str]:
    """Endpoint that simulates a long-running job by failing a few times before succeeding.

    This endpoint is designed to test HTTP polling functionality:
    - Returns 500 error for first 2 calls
    - Returns success message on 3rd call
    - Resets counter after success
    """
    global counter
    logger.info("Simulating job", counter=counter)
    if counter == 0:
        logger.info("Simulating job success")
        counter = 3
        return {
            "status": "success",
            "data": "Hello world. I am the Test Stress Server.",
        }
    else:
        counter -= 1
        logger.info("Simulating job failure")
        return {"status": "loading"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8989)
