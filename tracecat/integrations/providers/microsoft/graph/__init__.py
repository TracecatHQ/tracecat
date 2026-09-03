"""Microsoft Graph OAuth providers."""

from tracecat.integrations.providers.microsoft.graph.entra import (
    MicrosoftEntraACProvider,
    MicrosoftEntraCCProvider,
)
from tracecat.integrations.providers.microsoft.graph.outlook import (
    MicrosoftOutlookACProvider,
    MicrosoftOutlookCCProvider,
)
from tracecat.integrations.providers.microsoft.graph.provider import (
    MicrosoftGraphACProvider,
    MicrosoftGraphCCProvider,
)
from tracecat.integrations.providers.microsoft.graph.security import (
    MicrosoftGraphSecurityACProvider,
    MicrosoftGraphSecurityCCProvider,
)
from tracecat.integrations.providers.microsoft.graph.teams import (
    MicrosoftTeamsACProvider,
    MicrosoftTeamsCCProvider,
)

__all__ = [
    "MicrosoftEntraACProvider",
    "MicrosoftEntraCCProvider",
    "MicrosoftGraphACProvider",
    "MicrosoftGraphCCProvider",
    "MicrosoftGraphSecurityACProvider",
    "MicrosoftGraphSecurityCCProvider",
    "MicrosoftOutlookACProvider",
    "MicrosoftOutlookCCProvider",
    "MicrosoftTeamsACProvider",
    "MicrosoftTeamsCCProvider",
]
