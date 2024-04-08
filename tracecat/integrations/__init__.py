"""Integrations module.

Creating new integrations
-------------------------
1. Optionally create a new integration platform/namespace and add a new platform icon in frontend/src/components/icons.tsx
2. Create a new integration function in an integration namespace.
3. Register the integration function with the registry singleton using `@registry.register`.
4. [IMPORTANT] Import the integration function in this file (integrations/__init__.py). This eagerly registers the integration.
5. Update `ActionType` in types/actions.py
6. (Frontend) Update `integrationTypes` in frontend/src/types/schemas.ts
"""

# Import modules to register integrations
from tracecat.integrations import datadog, example, material_security
from tracecat.integrations._meta import IntegrationSpec
from tracecat.integrations._registry import registry

__all__ = ["IntegrationSpec", "registry", "example", "material_security", "datadog"]
