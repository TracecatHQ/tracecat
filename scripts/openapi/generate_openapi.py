#!/usr/bin/env python3
"""Generate OpenAPI spec from FastAPI app without running server."""

import json
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Import and configure logger before importing the app
from tracecat.logger import logger  # noqa: E402

logger.remove()  # Remove all handlers
logger.add(lambda _: None)  # Add a no-op handler to prevent any output


def main() -> None:
    """Generate OpenAPI spec and write to stdout or file."""
    from tracecat.api.app import app

    openapi_spec = app.openapi()

    if len(sys.argv) > 1:
        # Write to file if path provided
        output_path = Path(sys.argv[1])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(openapi_spec, indent=2))
        print(f"OpenAPI spec written to {output_path}", file=sys.stderr)
    else:
        # Write to stdout
        print(json.dumps(openapi_spec, indent=2))


if __name__ == "__main__":
    main()
