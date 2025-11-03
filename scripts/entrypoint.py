#!/usr/bin/python
import os
import sys
import subprocess

TRUTHY = {"1", "true", "yes", "on", "y", "t"}

def as_bool(val: str) -> bool:
    return val.lower() in TRUTHY

def run_migrations() -> bool:
    print("Running database migrations...")
    try:
        # Use the same interpreter; no shell needed.
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            check=True,
        )
    except subprocess.CalledProcessError as e:
        print("Migration failed!", flush=True)
        return False
    print("Migrations completed successfully.", flush=True)
    return True

def main():
    # Only run migrations when explicitly requested
    run_flag = os.getenv("RUN_MIGRATIONS", "false")
    if as_bool(run_flag):
        if not run_migrations():
            print("Exiting due to migration failure", flush=True)
            sys.exit(1)

    # If args were provided, exec them with the current Python
    # This mirrors `exec "$@"` from the Bash script.
    args = sys.argv[1:]
    if args:
        os.execv(sys.executable, [sys.executable] + args)

    # Otherwise, run uvicorn with HOST/PORT from env
    host = os.getenv("HOST", "0.0.0.0")
    port = os.getenv("PORT", "8000")
    os.execv(
        sys.executable,
        [sys.executable, "-m", "uvicorn", "tracecat.api.app:app",
         "--host", host, "--port", str(port)],
    )

if __name__ == "__main__":
    main()

