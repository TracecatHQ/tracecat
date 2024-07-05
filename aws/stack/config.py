import os

# NOTE: All public / service API urls are equivalent
# as this deployment is fully airgapped inside a
# single security group with no external internet access

# Used to pull Tracecat image from ghcr via the version tag
TRACECAT_VERSION = os.getenv("TRACECAT_VERSION", "latest")
TRACECAT_IMAGE = f"ghcr.io/tracecathq/tracecat:{TRACECAT_VERSION}"
TRACECAT_UI_IMAGE = f"ghcr.io/tracecathq/tracecat-ui:{TRACECAT_VERSION}"
