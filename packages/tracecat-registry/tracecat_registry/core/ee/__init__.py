"""Enterprise Edition SDK-only UDFs.

These UDFs are always registered but route to internal endpoints that are
gated by entitlements on the server side.
"""

import sys

from tracecat_registry.core import tasks as _tasks

# Case tasks moved to ``tracecat_registry.core.tasks``. Keep the old module path
# importable so registry versions synced before the move still resolve.
sys.modules[f"{__name__}.tasks"] = _tasks
