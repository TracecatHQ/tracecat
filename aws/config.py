import os

# NOTE: All public / service API urls are equivalent
# as this deployment is fully airgapped inside a
# single security group with no external internet access

# Used to pull Tracecat image from ghcr via the version tag
TRACECAT_VERSION = os.getenv("TRACECAT_VERSION", "latest")
TRACECAT_IMAGE = f"ghcr.io/tracecathq/tracecat:{TRACECAT_VERSION}"

# Application Environment
TRACECAT_APP_ENV = os.getenv("TRACECAT_APP_ENV", "development")
NODE_ENV = os.getenv("NODE_ENV", TRACECAT_APP_ENV)
NEXT_PUBLIC_APP_ENV = os.getenv("NEXT_PUBLIC_APP_ENV", TRACECAT_APP_ENV)

# Core service URLs
TRACECAT__API_URL = "http://api:8000"
TRACECAT__PUBLIC_API_URL = "http://api:8000"
TRACECAT__PUBLIC_RUNNER_URL = "http://worker:8001"

# URLs to add into frontend
NEXT_PUBLIC_API_URL = "http://api:8000"
NEXT_PUBLIC_APP_URL = "http://ui:3000"
NEXT_SERVER_API_URL = "http://api:8000"

# Temporal seervice URLs
TEMPORAL__CLUSTER_URL = "http://temporal:7233"
TEMPORAL__UI_URL = "http://temporal:8080"
