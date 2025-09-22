#!/usr/bin/env python
"""Direct load test for executor API to reproduce queue pool overflow issue.

This script directly hits the executor API endpoint to simulate concurrent load.
Run with: python tests/benchmark/test_executor_load_api.py
"""

import asyncio
import os
import time
import uuid
from typing import Any

import httpx

# Get service key from environment
SERVICE_KEY = os.environ.get("TRACECAT__SERVICE_KEY")
if not SERVICE_KEY:
    print("Error: TRACECAT__SERVICE_KEY not set in environment")
    exit(1)

# Executor API configuration
EXECUTOR_URL = "http://localhost:8001"
EXECUTOR_ENDPOINT = f"{EXECUTOR_URL}/run/core.http_request"
DB_POOL_ENDPOINT = f"{EXECUTOR_URL}/health/db-pool"

# Headers for service authentication
SERVICE_HEADERS = {
    "x-tracecat-service-key": SERVICE_KEY,
    "x-tracecat-role-service-id": "tracecat-runner",
    "x-tracecat-role-access-level": "ADMIN",
    "x-tracecat-role-workspace-id": "7724372b-4bc9-44de-b3e0-1a7275396bfb",
}


def create_test_payload() -> dict[str, Any]:
    """
    Create a test RunActionInput payload targeting the /wait endpoint of the benchmark server.

    Returns:
        dict[str, Any]: The payload for the executor API.
    """
    # Generate unique workflow and execution IDs for traceability
    wf_id: str = f"wf-{str(uuid.uuid4()).replace('-', '')}"
    wf_exec_id: str = f"{wf_id}/exec_{int(time.time() * 1000)}"
    wf_run_id: str = str(uuid.uuid4())

    # The /wait endpoint expects a float wait_time query param (default 1.0)
    # We'll use the default (1.0) for consistent benchmarking
    wait_url: str = "http://host.docker.internal:8008/wait"

    # Construct the payload for the executor API
    payload: dict[str, Any] = {
        "task": {
            "action": "core.http_request",
            "args": {
                "url": wait_url,
                "method": "GET",
                "params": {"wait_time": "1"},
            },
            "ref": f"test_ref_{int(time.time() * 1000000)}",
        },
        "exec_context": {
            "ACTIONS": {},
        },
        "run_context": {
            "wf_id": wf_id,
            "wf_exec_id": wf_exec_id,
            "wf_run_id": wf_run_id,
            "environment": "test",
        },
    }

    # Validate payload types for strictness
    if not isinstance(payload, dict):
        raise TypeError("Payload must be a dictionary")
    if not isinstance(payload["task"], dict):
        raise TypeError("Payload['task'] must be a dictionary")
    if not isinstance(payload["task"]["args"], dict):
        raise TypeError("Payload['task']['args'] must be a dictionary")
    if not isinstance(payload["exec_context"], dict):
        raise TypeError("Payload['exec_context'] must be a dictionary")
    if not isinstance(payload["run_context"], dict):
        raise TypeError("Payload['run_context'] must be a dictionary")

    return payload


async def check_db_pool_status(client: httpx.AsyncClient) -> dict[str, Any]:
    """Check the database pool status."""
    response = await client.get(DB_POOL_ENDPOINT)
    if response.status_code == 200:
        return response.json()
    return {"error": f"Status {response.status_code}: {response.text}"}


async def get_postgres_connections() -> dict[str, Any]:
    """Get PostgreSQL connection statistics directly from the database."""
    import subprocess

    try:
        # Query to get connection counts by state
        query = """
        SELECT
            state,
            COUNT(*) as count,
            application_name
        FROM pg_stat_activity
        WHERE datname = 'postgres'
        GROUP BY state, application_name
        ORDER BY count DESC;
        """

        # Run psql command in the postgres container
        cmd = [
            "docker",
            "exec",
            "tracecat-postgres_db-1",
            "psql",
            "-U",
            "postgres",
            "-d",
            "postgres",
            "-t",
            "-c",
            query,
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)

        if result.returncode == 0:
            # Parse the output
            lines = result.stdout.strip().split("\n")
            connections = []
            total_active = 0
            total_idle = 0

            for line in lines:
                if line.strip():
                    parts = [p.strip() for p in line.split("|")]
                    if len(parts) >= 3:
                        state = parts[0] if parts[0] else "active"
                        count = int(parts[1]) if parts[1].isdigit() else 0
                        app_name = parts[2] if len(parts) > 2 else "unknown"

                        connections.append(
                            {"state": state, "count": count, "app_name": app_name}
                        )

                        if state == "active":
                            total_active += count
                        elif state == "idle":
                            total_idle += count

            # Also get total connection count
            total_query = (
                "SELECT COUNT(*) FROM pg_stat_activity WHERE datname = 'postgres';"
            )
            total_cmd = [
                "docker",
                "exec",
                "tracecat-postgres_db-1",
                "psql",
                "-U",
                "postgres",
                "-d",
                "postgres",
                "-t",
                "-c",
                total_query,
            ]
            total_result = subprocess.run(
                total_cmd, capture_output=True, text=True, timeout=5
            )
            total_connections = (
                int(total_result.stdout.strip()) if total_result.returncode == 0 else -1
            )

            return {
                "total": total_connections,
                "active": total_active,
                "idle": total_idle,
                "by_state": connections,
            }
        else:
            return {"error": f"psql command failed: {result.stderr}"}

    except subprocess.TimeoutExpired:
        return {"error": "PostgreSQL query timed out"}
    except Exception as e:
        return {"error": str(e)}


async def reset_db_pool(client: httpx.AsyncClient) -> dict[str, Any]:
    """Reset/purge the database pool connections."""
    # Try to reset the pool via the health endpoint with a reset parameter
    try:
        response = await client.get(
            f"{EXECUTOR_URL}/health/db-pool/reset",
            headers=SERVICE_HEADERS,
            timeout=10.0,
        )
        if response.status_code == 200:
            return response.json()
        elif response.status_code == 404:
            # Endpoint doesn't exist, try alternative approach
            print(
                "  DB pool reset endpoint not available, waiting for natural timeout..."
            )
            return {"status": "reset_unavailable"}
        else:
            return {"error": f"Status {response.status_code}: {response.text}"}
    except Exception as e:
        return {"error": str(e)}


async def wait_for_pool_recovery(
    client: httpx.AsyncClient, max_wait: float = 60.0
) -> bool:
    """Wait for the DB pool to recover to a healthy state."""
    print("\n  Waiting for DB pool to recover...")
    start_time = time.time()
    last_status = None

    while time.time() - start_time < max_wait:
        status = await check_db_pool_status(client)
        if "error" not in status:
            checked_out = status.get("checked_out", 0)
            overflow = status.get("overflow", 0)

            # Show status if it changed
            current_status = (checked_out, overflow)
            if current_status != last_status:
                elapsed = time.time() - start_time
                print(
                    f"    T+{elapsed:.1f}s: Checked out={checked_out}, Overflow={overflow}"
                )
                last_status = current_status

            # Consider pool recovered if checked out is low and no overflow
            if checked_out <= 5 and overflow <= 0:
                print(f"  ✅ Pool recovered after {time.time() - start_time:.1f}s")
                return True

        await asyncio.sleep(2.0)

    print(f"  ⚠️ Pool did not fully recover after {max_wait}s")
    return False


async def execute_single_action(
    client: httpx.AsyncClient, index: int
) -> tuple[int, float, int, str | None]:
    """Execute a single action and return timing info."""

    start = time.time()
    payload = create_test_payload()

    try:
        try:
            response = await client.post(
                EXECUTOR_ENDPOINT,
                json=payload,
                headers=SERVICE_HEADERS,
                timeout=600.0,
            )
        except httpx.RemoteProtocolError:
            # One-shot retry on transient disconnects from stale keep-alive sockets
            response = await client.post(
                EXECUTOR_ENDPOINT,
                json=payload,
                headers=SERVICE_HEADERS,
                timeout=600.0,
            )

        elapsed = time.time() - start

        if response.status_code == 200:
            return index, elapsed, response.status_code, None
        else:
            error_msg = f"Status {response.status_code}: {response.text[:200]}"
            return index, elapsed, response.status_code, error_msg

    except httpx.TimeoutException:
        elapsed = time.time() - start
        error_msg = f"TimeoutException: Request timed out after {elapsed:.1f}s"
        return index, elapsed, -1, error_msg
    except httpx.ConnectError as e:
        elapsed = time.time() - start
        error_msg = f"ConnectError: {str(e)}"
        return index, elapsed, -1, error_msg
    except httpx.RemoteProtocolError as e:
        # If retry also failed, record explicitly
        elapsed = time.time() - start
        error_msg = (
            f"RemoteProtocolError: {str(e)}" if str(e) else "RemoteProtocolError"
        )
        return index, elapsed, -1, error_msg
    except Exception as e:
        elapsed = time.time() - start
        error_msg = f"{type(e).__name__}: {str(e)}" if str(e) else type(e).__name__
        return index, elapsed, -1, error_msg


async def run_concurrent_test(concurrency: int) -> dict[str, Any]:
    """Run concurrent requests and collect metrics."""
    print(f"\n{'=' * 60}")
    print(f"Testing with {concurrency} concurrent requests...")
    print(f"{'=' * 60}")

    # Configure client connection limits. Keep keepalive_expiry small to avoid stale sockets.
    limits = httpx.Limits(
        max_keepalive_connections=200,
        max_connections=1000,
        keepalive_expiry=3.0,
    )
    async with httpx.AsyncClient(limits=limits) as client:
        # Check initial pool status
        initial_pool = await check_db_pool_status(client)
        initial_pg = await get_postgres_connections()
        print("\nInitial DB Pool Status:")
        if "error" not in initial_pool:
            print(
                f"  SQLAlchemy Pool: size={initial_pool.get('pool_size', 'N/A')}, "
                f"checked_out={initial_pool.get('checked_out', 'N/A')}, "
                f"overflow={initial_pool.get('overflow', 'N/A')}"
            )
        else:
            print(f"  Error: {initial_pool['error']}")

        if "error" not in initial_pg:
            print(
                f"  PostgreSQL: total={initial_pg.get('total', 'N/A')}, "
                f"active={initial_pg.get('active', 'N/A')}, "
                f"idle={initial_pg.get('idle', 'N/A')}"
            )
        else:
            print(f"  PostgreSQL Error: {initial_pg['error']}")

        # Run concurrent requests
        tasks = [execute_single_action(client, i) for i in range(concurrency)]
        start = time.time()
        results = await asyncio.gather(*tasks)
        total_time = time.time() - start

        # Check final pool status
        final_pool = await check_db_pool_status(client)
        final_pg = await get_postgres_connections()
        print("\nFinal DB Pool Status:")
        if "error" not in final_pool:
            print(
                f"  SQLAlchemy Pool: size={final_pool.get('pool_size', 'N/A')}, "
                f"checked_out={final_pool.get('checked_out', 'N/A')}, "
                f"overflow={final_pool.get('overflow', 'N/A')}"
            )
        else:
            print(f"  Error: {final_pool['error']}")

        if "error" not in final_pg:
            print(
                f"  PostgreSQL: total={final_pg.get('total', 'N/A')}, "
                f"active={final_pg.get('active', 'N/A')}, "
                f"idle={final_pg.get('idle', 'N/A')}"
            )
        else:
            print(f"  PostgreSQL Error: {final_pg['error']}")

        # Analyze results
        successes = [r for r in results if r[2] == 200]
        failures = [r for r in results if r[2] != 200]

        metrics = {
            "concurrency": concurrency,
            "total_requests": len(results),
            "successful": len(successes),
            "failed": len(failures),
            "total_time": total_time,
            "requests_per_second": len(results) / total_time if total_time > 0 else 0,
        }

        if successes:
            success_times = [r[1] for r in successes]
            metrics["avg_success_time"] = sum(success_times) / len(success_times)
            metrics["min_success_time"] = min(success_times)
            metrics["max_success_time"] = max(success_times)

        if failures:
            # Group failures by error type and message
            failure_details = {}
            for _, _, status_code, error in failures:
                if status_code == -1:
                    # Parse exception messages to group similar errors
                    if error:
                        error_lower = error.lower()
                        if (
                            "connection pool is full" in error_lower
                            or "pool overflow" in error_lower
                            or "queuepool" in error_lower
                        ):
                            key = "DB_POOL_OVERFLOW"
                        elif "timeout" in error_lower:
                            key = "TIMEOUT"
                        elif "connection refused" in error_lower:
                            key = "CONNECTION_REFUSED"
                        elif (
                            "read timeout" in error_lower
                            or "readtimeout" in error_lower
                        ):
                            key = "READ_TIMEOUT"
                        elif (
                            "connectionerror" in error_lower
                            or "connection error" in error_lower
                        ):
                            key = "CONNECTION_ERROR"
                        elif "httpcore" in error_lower or "httpx" in error_lower:
                            key = "HTTP_CLIENT_ERROR"
                        else:
                            # Include the exception type in the key for better grouping
                            if ":" in error:
                                exception_type = error.split(":")[0].strip()
                                key = f"EXCEPTION_{exception_type}"
                            else:
                                key = "OTHER_EXCEPTION"
                    else:
                        key = "UNKNOWN_EXCEPTION"
                else:
                    key = f"HTTP_{status_code}"

                if key not in failure_details:
                    failure_details[key] = {"count": 0, "samples": []}

                failure_details[key]["count"] += 1
                # Store up to 3 unique error messages per type
                if error and len(failure_details[key]["samples"]) < 3:
                    # Truncate very long errors
                    sample = error[:500] + "..." if len(error) > 500 else error
                    if sample not in failure_details[key]["samples"]:
                        failure_details[key]["samples"].append(sample)

            metrics["failure_details"] = failure_details

            # Display detailed error breakdown
            print("\n📊 Failure Analysis:")
            print(
                f"  Total failures: {len(failures)}/{len(results)} ({len(failures) * 100 / len(results):.1f}%)"
            )
            print("\n  Failure types:")
            for error_type, details in sorted(
                failure_details.items(), key=lambda x: x[1]["count"], reverse=True
            ):
                percentage = (details["count"] / len(failures)) * 100
                print(
                    f"    • {error_type}: {details['count']} failures ({percentage:.1f}%)"
                )

                # Show sample error messages
                if details["samples"]:
                    for i, sample in enumerate(details["samples"], 1):
                        # Indent error messages for readability
                        indented_sample = sample.replace("\n", "\n        ")
                        print(f"      Sample {i}: {indented_sample}")

        print("\n📈 Performance Metrics:")
        print(f"  Successful: {metrics['successful']}/{metrics['total_requests']}")
        print(f"  Failed: {metrics['failed']}/{metrics['total_requests']}")
        print(f"  Total time: {metrics['total_time']:.2f}s")
        print(f"  Requests/sec: {metrics['requests_per_second']:.2f}")

        if "avg_success_time" in metrics:
            print(f"  Avg response time: {metrics['avg_success_time']:.3f}s")
            print(f"  Min response time: {metrics['min_success_time']:.3f}s")
            print(f"  Max response time: {metrics['max_success_time']:.3f}s")

        return metrics


async def main():
    """Run load tests with increasing concurrency."""
    print("\n" + "=" * 60)
    print("EXECUTOR SERVICE LOAD TEST")
    print("Target:", EXECUTOR_URL)
    print("=" * 60)

    # Test with increasing load
    # concurrency_levels = [100, 200, 500, 1000, 5000, 10000]
    concurrency_levels = [200, 500, 1000]
    all_results = []

    for concurrency in concurrency_levels:
        try:
            metrics = await run_concurrent_test(concurrency)
            all_results.append(metrics)

            # If we see failures, provide analysis
            if metrics["failed"] > 0:
                failure_rate = (metrics["failed"] / metrics["total_requests"]) * 100
                print(f"\n⚠️  Failure rate: {failure_rate:.1f}%")

                if failure_rate > 50:
                    print(
                        "\n🔴 High failure rate detected! This indicates the queue pool overflow issue."
                    )
                    print(
                        "   The executor cannot handle this level of concurrent load."
                    )

            # Wait between tests and ensure pool recovery
            if concurrency < concurrency_levels[-1]:
                print(f"\n{'=' * 40}")
                print("Preparing for next test...")
                print(f"{'=' * 40}")

                async with httpx.AsyncClient() as recovery_client:
                    # First try to reset the pool
                    reset_result = await reset_db_pool(recovery_client)
                    if (
                        "error" not in reset_result
                        and reset_result.get("status") != "reset_unavailable"
                    ):
                        print("  DB pool reset initiated")

                    # Wait for pool to recover
                    recovered = await wait_for_pool_recovery(recovery_client)

                    if not recovered:
                        print(
                            "\n⚠️ Warning: DB pool still saturated. Results may be affected."
                        )
                        print(
                            "  Consider restarting the executor service to fully clear connections."
                        )
                        # Still continue with the test but warn the user

        except Exception as e:
            print(f"\n❌ Test failed for concurrency={concurrency}: {e}")
            break

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)

    for result in all_results:
        status = "✅" if result["failed"] == 0 else "❌"
        print(
            f"{status} Concurrency {result['concurrency']:3d}: "
            f"{result['successful']:3d}/{result['total_requests']:3d} succeeded, "
            f"{result['requests_per_second']:.1f} req/s"
        )

    # Identify the breaking point
    for i, result in enumerate(all_results):
        if result["failed"] > 0:
            if i == 0:
                print(
                    f"\n🔴 System fails even at low concurrency ({result['concurrency']})"
                )
            else:
                prev = all_results[i - 1]
                print(
                    f"\n🔴 Breaking point between {prev['concurrency']} and {result['concurrency']} concurrent requests"
                )
            break
    else:
        if all_results:
            print(
                f"\n✅ System handled up to {all_results[-1]['concurrency']} concurrent requests"
            )


if __name__ == "__main__":
    asyncio.run(main())
