"""Enterprise Edition shim module.

This module contains shims and imports for Tracecat Enterprise Edition features.
All actual EE functionality is implemented in the separate `tracecat-ee` package.
When EE features are not installed, appropriate ImportError exceptions are raised
to guide users to install the required extras.
"""
