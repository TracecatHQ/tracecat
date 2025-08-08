# Shim for EE service - requires EE installation
try:
    from tracecat_ee.interactions.service import *  # noqa: F401,F403
except ImportError as exc:
    raise ImportError(
        "Tracecat Enterprise features are not installed. "
        "Install with extras: `pip install 'tracecat[ee]'`."
    ) from exc
