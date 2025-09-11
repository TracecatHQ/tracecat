import asyncio
import time
from datetime import datetime
from typing import Any

from fastapi import FastAPI, Query
from pydantic import BaseModel

app = FastAPI(
    title="Benchmark Server", description="A simple FastAPI server for testing"
)


class DataResponse(BaseModel):
    timestamp: str
    message: str
    data: dict[str, Any]
    wait_time: float


@app.get("/")
async def root():
    """Root endpoint that returns basic info"""
    return {
        "message": "FastAPI Benchmark Server",
        "timestamp": datetime.now().isoformat(),
        "endpoints": ["/", "/wait", "/sync-wait", "/data"],
    }


@app.get("/wait", response_model=DataResponse)
async def wait_and_return_data(
    wait_time: float = Query(
        default=1.0, ge=0, le=10, description="Wait time in seconds"
    ),
):
    """Async endpoint that waits for specified time and returns data"""
    start_time = time.time()

    # Async wait
    await asyncio.sleep(wait_time)

    actual_wait_time = time.time() - start_time

    return DataResponse(
        timestamp=datetime.now().isoformat(),
        message=f"Waited {actual_wait_time:.2f} seconds asynchronously",
        data={
            "requested_wait": wait_time,
            "actual_wait": actual_wait_time,
            "method": "async",
            "random_data": {
                "numbers": [1, 2, 3, 4, 5],
                "nested": {"key": "value", "count": 42},
            },
        },
        wait_time=actual_wait_time,
    )


@app.get("/sync-wait", response_model=DataResponse)
def sync_wait_and_return_data(
    wait_time: float = Query(
        default=1.0, ge=0, le=10, description="Wait time in seconds"
    ),
):
    """Sync endpoint that waits for specified time and returns data"""
    start_time = time.time()

    # Sync wait
    time.sleep(wait_time)

    actual_wait_time = time.time() - start_time

    return DataResponse(
        timestamp=datetime.now().isoformat(),
        message=f"Waited {actual_wait_time:.2f} seconds synchronously",
        data={
            "requested_wait": wait_time,
            "actual_wait": actual_wait_time,
            "method": "sync",
            "random_data": {
                "letters": ["a", "b", "c", "d", "e"],
                "nested": {"sync": True, "counter": 100},
            },
        },
        wait_time=actual_wait_time,
    )


@app.get("/data")
async def get_sample_data():
    """Endpoint that returns sample data without waiting"""
    return {
        "timestamp": datetime.now().isoformat(),
        "message": "Sample data without wait",
        "data": {
            "users": [
                {"id": 1, "name": "Alice", "active": True},
                {"id": 2, "name": "Bob", "active": False},
                {"id": 3, "name": "Charlie", "active": True},
            ],
            "metrics": {
                "total_requests": 1234,
                "average_response_time": 0.15,
                "uptime_hours": 72.5,
            },
            "status": "healthy",
        },
    }


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8008)
