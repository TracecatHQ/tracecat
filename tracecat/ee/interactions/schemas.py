# Shim to re-export core models for backward compatibility
from tracecat.interactions.schemas import *  # noqa: F401,F403

# EE-specific models are re-exported from tracecat_ee when available
try:
    from tracecat_ee.interactions.service import (  # noqa: F401
        CreateInteractionActivityInputs,
        InteractionCreate,
        InteractionRead,
        InteractionUpdate,
        UpdateInteractionActivityInputs,
    )
except ImportError:
    # If EE is not installed, these models won't be available
    # This is expected for OSS-only installations
    pass
