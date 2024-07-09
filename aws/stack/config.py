import os

# NOTE: All public / service API urls are equivalent
# as this deployment is fully airgapped inside a
# single security group with no external internet access

# Used to pull Tracecat image from ghcr via the version tag
TRACECAT_VERSION = os.getenv("TRACECAT__VERSION", "latest")
TRACECAT_IMAGE = f"ghcr.io/tracecathq/tracecat:{TRACECAT_VERSION}"
TRACECAT_UI_IMAGE = f"ghcr.io/tracecathq/tracecat-ui:{TRACECAT_VERSION}"

# Temporal images
TEMPORAL_VERSION = os.getenv("TEMPORAL__VERSION", "latest")
TEMPORAL_UI_VERSION = os.getenv("TEMPORAL__UI_VERSION", "latest")
TEMPORAL_SERVER_IMAGE = f"temporalio/auto-setup:{TEMPORAL_VERSION}"
TEMPORAL_UI_IMAGE = f"temporalio/ui:{TEMPORAL_UI_VERSION}"

# DNS
APP_DOMAIN_NAME = os.getenv("APP_DOMAIN_NAME", "tracecat.com")

# Whitelist
ALB_ALLOWED_CIDR_BLOCKS = os.getenv("ALB_ALLOWED_CIDR_BLOCKS", "0.0.0.0/0").split(",")
