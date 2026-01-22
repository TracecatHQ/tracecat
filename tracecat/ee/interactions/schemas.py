# Shim to re-export core models for backward compatibility
from tracecat.interactions.schemas import *  # noqa: F401,F403

# EE-specific models are re-exported from tracecat_ee when available
try:
    from tracecat_ee.interactions.service import (  # noqa: F401
        CreateInteractionActivityInputs as CreateInteractionActivityInputs,
    )
    from tracecat_ee.interactions.service import (
        InteractionCreate as InteractionCreate,
    )
    from tracecat_ee.interactions.service import (
        InteractionRead as InteractionRead,
    )
    from tracecat_ee.interactions.service import (
        InteractionUpdate as InteractionUpdate,
    )
    from tracecat_ee.interactions.service import (
        UpdateInteractionActivityInputs as UpdateInteractionActivityInputs,
    )
except ImportError:
    # If EE is not installed, these models won't be available
    # This is expected for OSS-only installations
    pass
