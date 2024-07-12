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
APP_DOMAIN_NAME = os.environ["TRACECAT__APP_URL"].replace("https://", "")
API_DOMAIN_NAME = os.environ["TRACECAT__PUBLIC_RUNNER_URL"].replace("https://", "")

# Certificates
CERTIFICATE_ARN = os.environ["CERTIFICATE_ARN"]
API_CERTIFICATE_ARN = os.environ["API_CERTIFICATE_ARN"]

# Whitelist
ALB_ALLOWED_CIDR_BLOCKS = os.getenv("ALB_ALLOWED_CIDR_BLOCKS", "").split(",")

# Production CPU and RAM configs
TRACECAT_API_CPU = int(os.getenv("TRACECAT__API_CPU", 1024))
TRACECAT_API_RAM = int(os.getenv("TRACECAT__API_RAM", 3072))

TRACECAT_WORKER_CPU = int(os.getenv("TRACECAT__WORKER_CPU", 2048))
TRACECAT_WORKER_RAM = int(os.getenv("TRACECAT__WORKER_RAM", 6144))

TRACECAT_UI_CPU = int(os.getenv("TRACECAT__UI_CPU", 2048))
TRACECAT_UI_RAM = int(os.getenv("TRACECAT__UI_RAM", 4096))

TEMPORAL_SERVER_CPU = int(os.getenv("TEMPORAL__SERVER_CPU", 2048))
TEMPORAL_SERVER_RAM = int(os.getenv("TEMPORAL__SERVER_RAM", 4096))

TEMPORAL_UI_CPU = int(os.getenv("TEMPORAL__UI_CPU", 256))
TEMPORAL_UI_RAM = int(os.getenv("TEMPORAL__UI_RAM", 512))
