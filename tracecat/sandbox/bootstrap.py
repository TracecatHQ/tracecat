"""Root entrypoint for preparing cgroups before starting the executor worker."""

from tracecat.sandbox.cgroup import main

if __name__ == "__main__":
    main()
