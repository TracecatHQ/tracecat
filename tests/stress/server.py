"""
This is a simple server that is used to test the stress of the Tracecat API.

Use a reverse tunnel like bore.pub to expose this server for testing.

For local stress testing, use the StressServer context manager:

    async with StressServer(port=8989) as server:
        # server is running at http://localhost:8989
        # run your tests here
"""

import asyncio
import json
import os
import random
import socket
from multiprocessing import Process
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query

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


@app.get("/io-work")
async def io_work(
    delay_ms: int = Query(default=100, ge=0, le=10000),
    jitter_ms: int = Query(default=50, ge=0, le=5000),
) -> dict[str, float | str]:
    """Endpoint that simulates IO-bound work with configurable delay.

    Args:
        delay_ms: Base delay in milliseconds (default 100ms)
        jitter_ms: Random jitter added to delay (default 50ms, so 100-150ms total)

    Returns:
        Dict with actual delay and a message
    """
    actual_delay_ms = delay_ms + random.randint(0, jitter_ms)
    await asyncio.sleep(actual_delay_ms / 1000.0)
    return {
        "delay_ms": actual_delay_ms,
        "message": "IO work completed",
    }


@app.post("/io-work")
async def io_work_post(
    delay_ms: int = Query(default=100, ge=0, le=10000),
    jitter_ms: int = Query(default=50, ge=0, le=5000),
) -> dict[str, float | str]:
    """POST version of io-work endpoint."""
    return await io_work(delay_ms=delay_ms, jitter_ms=jitter_ms)


def _find_free_port() -> int:
    """Find a free port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        s.listen(1)
        return s.getsockname()[1]


def _run_server(port: int) -> None:
    """Run the uvicorn server (called in subprocess)."""
    import uvicorn

    # Suppress uvicorn logs for cleaner test output
    # Bind to 0.0.0.0 so Docker containers can reach via host.docker.internal
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port,
        log_level="warning",
    )


class StressServer:
    """Context manager for running the stress server in a subprocess.

    Usage:
        async with StressServer() as server:
            url = server.url  # For local access: http://127.0.0.1:54321
            docker_url = server.docker_url  # For Docker: http://host.docker.internal:54321
            # run tests that hit this URL
    """

    def __init__(self, port: int | None = None):
        self.port = port or _find_free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self.docker_url = f"http://host.docker.internal:{self.port}"
        self._process: Process | None = None

    async def __aenter__(self) -> "StressServer":
        self._process = Process(target=_run_server, args=(self.port,), daemon=True)
        self._process.start()

        # Wait for server to be ready
        max_retries = 50
        for _i in range(max_retries):
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.settimeout(0.1)
                    s.connect(("127.0.0.1", self.port))
                    logger.info(
                        f"Stress server ready at {self.url} (docker: {self.docker_url})"
                    )
                    return self
            except (TimeoutError, ConnectionRefusedError):
                await asyncio.sleep(0.1)

        raise RuntimeError(f"Stress server failed to start on port {self.port}")

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._process is not None:
            self._process.terminate()
            self._process.join(timeout=5)
            if self._process.is_alive():
                self._process.kill()
                self._process.join(timeout=1)
            logger.info("Stress server stopped")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8989)
